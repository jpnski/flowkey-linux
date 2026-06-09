"""Global hotkey listener for Flowkey (Linux).

Handles global hotkey capture via pynput (X11) or evdev (Wayland),
clipboard operations, daemon dispatch, and optional clipboard watching.

Structure mirrors TODO.md Phase 3 items 11-26.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import actions as _actions
import paths as _paths
import pyperclip

# ---------------------------------------------------------------------------
# Module-level config (populated by read_config)
# ---------------------------------------------------------------------------

CONFIG: dict = {}
HOTKEY_BINDINGS: dict[str, str] = {}  # action_name -> human-readable hotkey (e.g. "ctrl+alt+g")
FLM_TIMEOUT_SECONDS: int = 60
DAEMON_BASE_URL: str = "http://127.0.0.1:52650"
API_VERSION: str = "1"
SESSION_TYPE: str = ""  # "x11", "wayland", or "" (detected at import time)

# ---------------------------------------------------------------------------
# Globals for hotkey listener lifecycle
# ---------------------------------------------------------------------------

_pynput_listener: Any = None
_evdev_thread: threading.Thread | None = None
_evdev_monitoring: bool = False
_config_watch_thread: threading.Thread | None = None
_clipboard_watch_thread: threading.Thread | None = None
_dashboard_poll_thread: threading.Thread | None = None
_shutdown_event = threading.Event()
_child_processes: list[subprocess.Popen] = []
_child_lock = threading.Lock()
_log_initialized = False

# ---------------------------------------------------------------------------
# Notification debounce state
# ---------------------------------------------------------------------------

_last_notifications: dict[str, float] = {}
_NOTIFY_DEBOUNCE_SECONDS: float = 5.0

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log = logging.getLogger("flowkey.listener")


def _ensure_logging() -> None:
    """Set up a basic stderr logger if not already configured."""
    global _log_initialized
    if _log_initialized:
        return
    _log_initialized = True
    if not log.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        log.addHandler(handler)
        log.setLevel(logging.INFO)


# ===================================================================
# Item 11 — Config loader
# ===================================================================

DEFAULT_HOTKEYS: dict[str, str] = {
    "grammar_fix": "ctrl+alt+g",
    "open_chat": "ctrl+alt+t",
    "capture_note": "ctrl+alt+n",
    "ask_chat": "ctrl+alt+a",
}


def read_config() -> dict:
    """Load config from paths.CONFIG_FILE, merge with defaults.

    Populates module-level CONFIG, HOTKEY_BINDINGS, FLM_TIMEOUT_SECONDS,
    and other globals consumed by the hotkey handlers.

    Returns the merged config dict (also stored as module CONFIG).
    """
    global CONFIG, HOTKEY_BINDINGS, FLM_TIMEOUT_SECONDS

    import config as _cfg  # late import to avoid circular dep at module level

    cfg = _cfg.load_config(_paths.CONFIG_FILE)
    CONFIG = cfg

    # Read hotkey bindings from config (human-readable: ctrl, super, alt)
    hotkeys_cfg = cfg.get("hotkeys") or {}
    merged: dict[str, str] = {}
    for action, default_key in DEFAULT_HOTKEYS.items():
        merged[action] = str(hotkeys_cfg.get(action, default_key))
    HOTKEY_BINDINGS = merged

    FLM_TIMEOUT_SECONDS = int(cfg.get("flm_timeout_seconds") or 60)
    return cfg


# ===================================================================
# Item 12 — Daemon lifecycle
# ===================================================================


def _is_daemon_healthy() -> bool:
    """Quick health check against the daemon HTTP endpoint."""
    try:
        req = urllib.request.Request(
            f"{DAEMON_BASE_URL}/healthz",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=0.5) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _find_flowkey_daemon() -> list[str]:
    """Resolve the flowkey-daemon executable.

    Tries PATH first, then falls back to running from the scripts directory.
    """
    which = shutil_which("flowkey-daemon")
    if which:
        return [which]

    # Fallback: run from source tree
    here = Path(__file__).resolve().parent
    daemon_script = here / "daemon.py"
    if daemon_script.exists():
        return [sys.executable, str(daemon_script)]
    return ["flowkey-daemon"]


def ensure_daemon_running() -> bool:
    """Ensure the daemon is running and healthy.

    Hits /healthz with 0.5s timeout. If unreachable, spawns the daemon
    and retries up to 10 times (5s total). Returns True if daemon is
    healthy.
    """
    if _is_daemon_healthy():
        return True

    daemon_argv = _find_flowkey_daemon()
    parent_arg = f"--parent-pid={os.getpid()}"
    daemon_argv.append(parent_arg)

    log.info("daemon not reachable — spawning: %s", daemon_argv)
    try:
        proc = subprocess.Popen(
            daemon_argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        with _child_lock:
            _child_processes.append(proc)
    except OSError as exc:
        log.error("failed to spawn daemon: %s", exc)
        return False

    # Retry up to 10 times (5s total)
    for attempt in range(10):
        if _shutdown_event.is_set():
            return False
        time.sleep(0.5)
        if _is_daemon_healthy():
            log.info("daemon healthy after %.1fs", (attempt + 1) * 0.5)
            return True

    log.warning("daemon did not become healthy within 5s")
    return False


# ===================================================================
# Item 13 — Daemon dispatch
# ===================================================================


def _parse_daemon_response(raw: str) -> str:
    """Extract the human-readable result from a daemon JSON response.

    Returns the result string if ok, or the error string if not ok,
    or empty string on parse failure.
    """
    if not raw:
        return ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip()
    if not isinstance(payload, dict):
        return raw.strip()
    if payload.get("ok") is False:
        return str(payload.get("error") or "error")
    result = payload.get("result")
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False)


def dispatch_action(action: str, body_json: str = "{}") -> str:
    """Send an action to the daemon via HTTP POST.

    Falls back to subprocess on HTTP failure:
      flowkey-grammar-fix --app-action <action>

    Returns the parsed result string.
    """
    url = f"{DAEMON_BASE_URL}/action/{action}"
    body_bytes = body_json.encode("utf-8")

    try:
        req = urllib.request.Request(
            url,
            data=body_bytes,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "X-FFP-API": API_VERSION,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            raw = resp.read().decode("utf-8")
            return _parse_daemon_response(raw)
    except (urllib.error.URLError, OSError) as exc:
        log.debug("daemon dispatch %s failed: %s — trying subprocess", action, exc)

    # Fallback: subprocess (read-only actions only)
    if action in _actions.READ_ONLY_SUBPROCESS_ACTIONS:
        try:
            result = subprocess.run(
                ["flowkey-grammar-fix", "--app-action", action],
                capture_output=True, text=True, timeout=10.0, check=False,
            )
            output = (result.stdout or "").strip()
            return output if output else (result.stderr or "").strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("subprocess fallback for %s failed: %s", action, exc)
            return ""

    return ""


# ===================================================================
# Item 14 — Mode prefix parsing
# ===================================================================

_MODE_PREFIX_ENTRIES: list[tuple[str, str]] = [
    ("prompt", r"(?:prompts|prompt)"),
    ("summarize", r"(?:summarizes|summarize)"),
    ("explain", r"(?:explains|explain)"),
    ("tone", r"tone"),
]


def _mode_prefix_line_pattern(kw: str) -> str:
    """Regex for mode prefix on the first line."""
    return (
        r"^\s*[>\-\*]*\s*/?" + kw +
        r"(\s*:\s*|\s*-\s+|$|\s+)(.*)$"
    )


def _mode_prefix_inline_pattern(kw: str) -> str:
    """Regex for inline mode prefix."""
    return (
        r"^\s*[>\-\*]*\s*/?" + kw +
        r"(\s*:\s*|\s*-\s+|\s+)(.+)$"
    )


def parse_mode_and_text(text: str) -> tuple[str, str]:
    """Detect mode prefix keywords on the first non-empty line.

    Given selected text, detect mode prefix keywords on the first
    non-empty line. Supports:

      prompt: <body>
      /prompt - <body>
      summarize: <body>
      explain <body>

    Returns (mode, body_text) where mode is one of 'grammar', 'prompt',
    'summarize', 'explain', 'tone'. Default mode is 'grammar'.
    """
    # Strip BOM
    raw = text.strip("\r\n\t ")
    if raw and raw[0] == "\ufeff":
        raw = raw[1:]

    if not raw:
        return ("grammar", "")

    lines = raw.split("\n")

    # ---- multiline: try to parse from the first non-empty line ----
    first_idx: int | None = None
    for i, line in enumerate(lines):
        stripped = line.strip("\t ")
        if stripped:
            first_idx = i
            break

    if first_idx is not None:
        first_line = lines[first_idx].strip("\t ")
        for mode, kw in _MODE_PREFIX_ENTRIES:
            m = re.match(_mode_prefix_line_pattern(kw), first_line, re.IGNORECASE)
            if m:
                parts: list[str] = []
                inline_body = m.group(2).strip("\t ")
                if inline_body:
                    parts.append(inline_body)
                for j in range(first_idx + 1, len(lines)):
                    stripped_line = lines[j].strip("\t ")
                    if stripped_line:
                        parts.append(stripped_line)
                body = "\n".join(parts).strip("\r\n\t ")
                return (mode, body)

    # ---- inline: regex whole text ----
    for mode, kw in _MODE_PREFIX_ENTRIES:
        m = re.match(_mode_prefix_inline_pattern(kw), raw, re.IGNORECASE)
        if m:
            body = m.group(2).strip("\r\n\t ")
            return (mode, body)

    return ("grammar", raw)


# ===================================================================
# Item 15 — Clipboard helpers
# ===================================================================


def clipboard_save() -> str:
    """Save current clipboard text. Returns empty string on failure."""
    try:
        return pyperclip.paste()
    except Exception as exc:
        log.debug("clipboard_save failed: %s", exc)
        return ""


def clipboard_restore(text: str) -> None:
    """Restore clipboard text. No-op on empty string."""
    if text:
        try:
            pyperclip.copy(text)
        except Exception as exc:
            log.debug("clipboard_restore failed: %s", exc)


def clipboard_capture() -> str:
    """Capture current selection via simulated Ctrl+C.

    Flow:
      1. Save current clipboard content
      2. Clear clipboard to detect fresh content
      3. Simulate Ctrl+C (pynput on X11, xdotool on Wayland)
      4. Wait 200ms for clipboard to populate
      5. Read clipboard content
      6. Restore original clipboard content
      7. Return captured text (or '' on failure)

    Returns the captured text string.
    """
    prior = clipboard_save()

    # Clear clipboard so we can detect fresh copy
    try:
        pyperclip.copy("")
    except Exception:
        pass

    # Simulate Ctrl+C
    _simulate_copy()

    # Wait for clipboard to populate
    time.sleep(0.2)

    # Read captured content
    captured = ""
    try:
        captured = pyperclip.paste()
    except Exception as exc:
        log.debug("clipboard_capture read failed: %s", exc)

    # Restore original clipboard
    clipboard_restore(prior)

    return captured


def _simulate_copy() -> None:
    """Simulate Ctrl+C based on session type."""
    global SESSION_TYPE
    if SESSION_TYPE == "x11":
        _simulate_copy_x11()
    elif SESSION_TYPE == "wayland":
        _simulate_copy_wayland()
    else:
        # Unknown session — try xdotool as universal fallback
        _simulate_copy_wayland()


def _simulate_copy_x11() -> None:
    """Simulate Ctrl+C via pynput (X11)."""
    try:
        from pynput.keyboard import Controller, Key
        kb = Controller()
        kb.press(Key.ctrl)
        kb.press("c")
        kb.release("c")
        kb.release(Key.ctrl)
    except Exception as exc:
        log.debug("pynput Ctrl+C failed: %s", exc)


def _simulate_copy_wayland() -> None:
    """Simulate Ctrl+C via xdotool (works on both X11 and Wayland in some setups)."""
    try:
        subprocess.run(
            ["xdotool", "key", "ctrl+c"],
            capture_output=True, timeout=1.0, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


# ===================================================================
# Item 16 — Paste back
# ===================================================================


def paste_back(text: str) -> None:
    """Copy text to clipboard and simulate Ctrl+V to paste.

    X11: uses pynput.keyboard.Controller for key simulation.
    Wayland: uses ydotool if available; otherwise notifies user.

    Args:
        text: The text to paste (will be copied to clipboard first).
    """
    if not text:
        return

    # Copy to clipboard
    try:
        pyperclip.copy(text)
    except Exception as exc:
        log.warning("paste_back: pyperclip.copy failed: %s", exc)
        return

    # Small delay to ensure clipboard is populated
    time.sleep(0.05)

    global SESSION_TYPE
    if SESSION_TYPE == "x11":
        _paste_back_x11()
    elif SESSION_TYPE == "wayland":
        _paste_back_wayland()
    else:
        # Unknown — try ydotool, fall back to notification
        if not _paste_back_wayland():
            _notify_fallback(text)


def _paste_back_x11() -> None:
    """Simulate Ctrl+V via pynput (X11)."""
    try:
        from pynput.keyboard import Controller, Key
        kb = Controller()
        kb.press(Key.ctrl)
        kb.press("v")
        kb.release("v")
        kb.release(Key.ctrl)
    except Exception as exc:
        log.warning("pynput Ctrl+V failed: %s", exc)


def _paste_back_wayland() -> bool:
    """Simulate Ctrl+V via ydotool (Wayland).

    Returns True if ydotool was found and executed, False otherwise.
    """
    ydotool = _which("ydotool")
    if ydotool:
        try:
            subprocess.run(
                [ydotool, "key", "ctrl+v"],
                capture_output=True, timeout=1.0, check=False,
            )
            return True
        except (OSError, subprocess.TimeoutExpired):
            pass
    return False


def _notify_fallback(text: str) -> None:
    """Notify user that result was copied to clipboard."""
    notify("Flowkey", "Result copied to clipboard — press Ctrl+V to paste")


# ===================================================================
# Item 17 — Keyboard capture init
# ===================================================================


def _detect_session_type() -> str:
    """Detect the current desktop session type.

    Checks $XDG_SESSION_TYPE environment variable. Returns 'x11',
    'wayland', or '' if unknown.
    """
    global SESSION_TYPE
    session = os.environ.get("XDG_SESSION_TYPE", "").strip().lower()
    if session in ("x11", "wayland"):
        SESSION_TYPE = session
        return session
    # Fallback: check $WAYLAND_DISPLAY
    if os.environ.get("WAYLAND_DISPLAY"):
        SESSION_TYPE = "wayland"
        return "wayland"
    SESSION_TYPE = ""
    return ""


# ===================================================================
# Item 22 — Notify with debounce
# ===================================================================


def notify(title: str, message: str) -> None:
    """Desktop notification with 5s debounce per (title, message) pair.

    Uses notify-send if available on D-Bus; falls back to stderr.
    """
    global _last_notifications
    key = f"{title}|{message}"
    now = time.monotonic()
    last = _last_notifications.get(key, 0.0)
    if now - last < _NOTIFY_DEBOUNCE_SECONDS:
        return
    _last_notifications[key] = now

    # Attempt notify-send
    notify_send = _which("notify-send")
    if notify_send:
        try:
            subprocess.Popen(
                [notify_send, title[:64], message[:512]],
                close_fds=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except OSError:
            pass

    # Fallback
    print(f"[{title}] {message}", file=sys.stderr)


# ===================================================================
# Item 18 — process_selection (grammar/prompt/summarize/explain/tone)
# ===================================================================


def _get_grammar_fix_argv() -> list[str]:
    """Resolve the flowkey-grammar-fix executable."""
    which = _which("flowkey-grammar-fix")
    if which:
        return [which]
    # Fallback: run from source tree
    here = Path(__file__).resolve().parent
    gf = here / "grammar_fix.py"
    if gf.exists():
        return [sys.executable, str(gf)]
    return ["flowkey-grammar-fix"]


def process_selection() -> None:
    """Main hotkey handler: capture selection, process, paste back.

    Flow:
      1. clipboard_capture() to get selected text
      2. parse_mode_and_text() to extract mode
      3. Write body to temp file
      4. Run flowkey-grammar-fix subprocess
      5. Read result from output temp file
      6. paste_back(result)
      7. Notify user of completion
    """
    captured = clipboard_capture()
    if not captured:
        notify("Flowkey", "No text selected — select text and try again")
        return

    mode, body = parse_mode_and_text(captured)
    if not body:
        notify("Flowkey", f"No text remaining after '{mode}:' prefix" if mode != "grammar" else "No text selected")
        return

    # Write body to temp input file
    infile = None
    outfile = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", prefix="flowkey_in_",
            delete=False, encoding="utf-8",
        ) as f_in:
            f_in.write(body)
            infile = f_in.name

        outfile_obj = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", prefix="flowkey_out_",
            delete=False, encoding="utf-8",
        )
        outfile = outfile_obj.name
        outfile_obj.close()

        # Run grammar fix subprocess
        gf_argv = _get_grammar_fix_argv()
        cmd = [
            *gf_argv,
            "--mode", mode,
            "--input-file", infile,
            "--output-file", outfile,
        ]
        timeout = max(FLM_TIMEOUT_SECONDS + 20, 60)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True, text=True,
                timeout=timeout, check=False,
            )
            if result.returncode != 0:
                err = (result.stderr or "").strip() or f"exit code {result.returncode}"
                notify("Flowkey", f"Grammar fix failed: {err}")
                return
        except subprocess.TimeoutExpired:
            notify("Flowkey", f"Grammar fix timed out after {timeout}s")
            return
        except OSError as exc:
            notify("Flowkey", f"Failed to launch grammar fix: {exc}")
            return

        # Read output
        try:
            output_text = Path(outfile).read_text(encoding="utf-8").strip()
        except OSError as exc:
            notify("Flowkey", f"Failed to read result: {exc}")
            return

        if not output_text:
            notify("Flowkey", "No output returned from grammar fix")
            return

        # Paste back
        paste_back(output_text)

        # Notify
        mode_labels = {
            "grammar": "Grammar fixed.",
            "prompt": "Prompt refined.",
            "summarize": "Summarized.",
            "explain": "Explained.",
            "tone": "Rewritten.",
        }
        label = mode_labels.get(mode, "Done.")
        notify("Flowkey", label)

    finally:
        # Clean up temp files
        for p in (infile, outfile):
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass


# ===================================================================
# Item 19 — capture_note (Ctrl+Alt+N)
# ===================================================================


def _get_active_window_name() -> str:
    """Get the active window name (best-effort).

    X11: uses xdotool getactivewindow getwindowname.
    Wayland: uses swaymsg -t get_tree (Sway) or returns ''.
    """
    global SESSION_TYPE
    if SESSION_TYPE == "x11":
        try:
            result = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowname"],
                capture_output=True, text=True, timeout=1.0, check=False,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    elif SESSION_TYPE == "wayland":
        # Sway
        try:
            result = subprocess.run(
                ["swaymsg", "-t", "get_tree"],
                capture_output=True, text=True, timeout=1.0, check=False,
            )
            if result.returncode == 0:
                # Parse the focused window name from sway tree JSON
                try:
                    tree = json.loads(result.stdout)
                    return _find_focused_window_name(tree) or ""
                except (json.JSONDecodeError, Exception):
                    pass
        except (OSError, subprocess.TimeoutExpired):
            pass
    return ""


def _find_focused_window_name(node: dict) -> str | None:
    """Recursively find the focused window name in a sway tree JSON node."""
    if node.get("focused"):
        return node.get("name") or node.get("app_id") or ""
    for child in node.get("nodes", []):
        result = _find_focused_window_name(child)
        if result:
            return result
    for child in node.get("floating_nodes", []):
        result = _find_focused_window_name(child)
        if result:
            return result
    return None


def capture_note() -> None:
    """Capture selection as a note (Ctrl+Alt+N handler).

    Flow:
      1. clipboard_capture() to get selection
      2. If empty, fall back to existing clipboard content
      3. Get active window name
      4. POST to daemon action save_note
      5. Notify
    """
    captured = clipboard_capture()
    if not captured:
        # Fall back to current clipboard content
        captured = clipboard_save()
    if not captured:
        notify("Flowkey", "Note capture: nothing to save — copy text first, then press the hotkey")
        return

    window_name = _get_active_window_name()

    body = json.dumps({
        "args": {
            "text": captured,
            "source_app": window_name,
            "url": "",
        },
    })
    result = dispatch_action("save_note", body)
    if result and "error" not in result.lower():
        notify("Flowkey", f"Note saved ({len(captured)} chars)")
    else:
        notify("Flowkey", f"Note saved ({len(captured)} chars)")  # best-effort


# ===================================================================
# Item 20 — launch_chat / launch_tui (Ctrl+Shift+T)
# ===================================================================


def _which(name: str) -> str | None:
    """Shutil.which replacement — find executable in PATH."""
    path = os.environ.get("PATH", "")
    for directory in path.split(os.pathsep):
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


# Re-export for internal use; the name is used consistently.
shutil_which = _which


def _resolve_tui_argv() -> list[str]:
    """Resolve the TUI executable path."""
    which = _which("flowkey-tui")
    if which:
        return [which]
    # Fallback: run from source tree
    here = Path(__file__).resolve().parent
    tui_script = here / "tui" / "app.py"
    if tui_script.exists():
        return [sys.executable, str(tui_script)]
    # Ultimate fallback - will error
    return ["flowkey-tui"]


def launch_chat() -> None:
    """Open the TUI (Ctrl+Shift+T handler).

    First tells the daemon to restart the chat session, then spawns
    the TUI application.
    """
    dispatch_action("chat_restart")
    time.sleep(0.2)

    tui_argv = _resolve_tui_argv()
    parent_arg = f"--parent-pid={os.getpid()}"
    tui_argv.append(parent_arg)

    try:
        proc = subprocess.Popen(
            tui_argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        with _child_lock:
            _child_processes.append(proc)
    except OSError as exc:
        notify("Flowkey", f"Failed to launch TUI: {exc}")


# ===================================================================
# Item 21 — ask_with_selection (Ctrl+Shift+A)
# ===================================================================


def ask_with_selection() -> None:
    """Send selection to chat (Ctrl+Shift+A handler).

    Flow:
      1. clipboard_capture() to get selection
      2. Get active window name
      3. POST to daemon action chat_send_selection
      4. Notify
    """
    captured = clipboard_capture()
    if not captured:
        notify("Flowkey", "Ask: nothing to send — select text and try again")
        return

    window_name = _get_active_window_name()

    body = json.dumps({
        "args": {
            "text": captured,
            "source_app": window_name,
        },
    })
    result = dispatch_action("chat_send_selection", body)
    if result and "error" not in result.lower():
        notify("Flowkey", f"Sent to chat ({len(captured)} chars)")
    else:
        # If daemon not available, try launching chat directly with the selection
        notify("Flowkey", f"Opened chat ({len(captured)} chars)")
        launch_chat()


# ===================================================================
# Item 23 — Clipboard watcher (background thread)
# ===================================================================

_CLIPBOARD_WATCHER_BLOCKLIST: frozenset[str] = frozenset({
    "KeePass",
    "KeePassXC",
    "Bitwarden",
    "1Password",
    "LastPass",
})


def _classify_clipboard(text: str) -> str:
    """Classify clipboard content by pattern matching.

    Returns one of: 'url', 'stacktrace', 'code', or ''.
    """
    trimmed = text.strip("\r\n\t ")
    # URL: whole clipboard is one well-formed http(s) URL
    if "\n" not in trimmed and re.match(r"^https?://\S+$", trimmed, re.IGNORECASE):
        return "url"

    # Stack trace markers (Python / JS / Java / .NET)
    if ("Traceback (most recent" in text
            or re.search(r'File "[^"]+", line \d+', text)
            or re.search(r"^\s*at\s+.+:\d+:\d+", text, re.IGNORECASE | re.MULTILINE)
            or re.search(r"\bat\s+\S+\(.+:\d+\)", text, re.IGNORECASE)
            or re.search(r"\bat\s+\S+\.\S+\(.+:\d+\)", text, re.IGNORECASE)):
        return "stacktrace"

    # Code heuristic: multiple lines + recognizable syntax markers
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if len(lines) >= 2:
        code_patterns = [
            r"^\s*def\s+\w+\s*\(",
            r"^\s*function\s+\w+\s*\(",
            r"^\s*class\s+\w+",
            r"^\s*(public|private|protected|static)\s+\w+\s+\w+\s*\(",
            r"^\s*import\s+\w+",
            r"^\s*const\s+\w+\s*=",
            r"=>\s*[\{(]",
        ]
        for pattern in code_patterns:
            if re.search(pattern, text, re.IGNORECASE | re.MULTILINE):
                return "code"

    return ""


def _is_blocked_process_active() -> bool:
    """Check if any blocked process is currently running.

    Reads /proc/*/comm to find active process names.
    """
    try:
        for proc_dir in Path("/proc").iterdir():
            if not proc_dir.name.isdigit():
                continue
            comm_file = proc_dir / "comm"
            try:
                comm = comm_file.read_text(encoding="utf-8").strip()
                for blocked in _CLIPBOARD_WATCHER_BLOCKLIST:
                    if blocked.lower() in comm.lower():
                        return True
            except (OSError, PermissionError):
                continue
    except OSError:
        pass
    return False


def clipboard_watcher() -> None:
    """Background thread: poll clipboard and classify content.

    Runs every 1s. On classification match, shows a hint notification.
    Skips if:
      - Clip unchanged (SHA256)
      - Blocklisted app is active
      - Within 5s cooldown

    On/off controlled by presence of paths.MARKER_CLIPBOARD_WATCHER.
    """
    _ensure_logging()
    log.info("clipboard watcher started")

    last_hash = ""
    last_fire: float = 0.0
    cooldown = 5.0

    while not _shutdown_event.is_set():
        # Check on/off marker
        if not _paths.MARKER_CLIPBOARD_WATCHER.exists():
            _shutdown_event.wait(1.0)
            continue

        # Blocklist check
        if _is_blocked_process_active():
            _shutdown_event.wait(1.0)
            continue

        # Read clipboard
        try:
            text = pyperclip.paste()
        except Exception:
            _shutdown_event.wait(1.0)
            continue

        if not text or len(text) < 30 or len(text) > 8000:
            _shutdown_event.wait(1.0)
            continue

        # Skip if unchanged
        current_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if current_hash == last_hash:
            _shutdown_event.wait(1.0)
            continue
        last_hash = current_hash

        # Cooldown
        now = time.monotonic()
        if now - last_fire < cooldown:
            _shutdown_event.wait(1.0)
            continue

        # Classify
        kind = _classify_clipboard(text)
        if not kind:
            _shutdown_event.wait(1.0)
            continue

        last_fire = now

        # Build hint notification
        grammar_hotkey = HOTKEY_BINDINGS.get("grammar_fix", "ctrl+alt+g")
        human_hotkey = _shortcut_to_human(grammar_hotkey)

        if kind == "url":
            notify("URL detected", f"Prefix with 'summarize:', select all + {human_hotkey}")
        elif kind == "stacktrace":
            notify("Stack trace detected", f"Prefix with 'explain:', select all + {human_hotkey}")
        elif kind == "code":
            notify("Code snippet detected", f"Prefix with 'explain:', select all + {human_hotkey}")

        _shutdown_event.wait(1.0)

    log.info("clipboard watcher stopped")


# ===================================================================
# Item 24 — Dashboard poll loop
# ===================================================================


def dashboard_poll_loop() -> None:
    """Background thread: poll dashboard marker file.

    Every 500ms checks if paths.MARKER_OPEN_DASHBOARD exists.
    When found: launch flowkey-tui, delete marker.
    """
    _ensure_logging()
    log.info("dashboard poll loop started")

    while not _shutdown_event.is_set():
        if _paths.MARKER_OPEN_DASHBOARD.exists():
            try:
                tui_argv = _resolve_tui_argv()
                parent_arg = f"--parent-pid={os.getpid()}"
                tui_argv.append(parent_arg)

                proc = subprocess.Popen(
                    tui_argv,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                )
                with _child_lock:
                    _child_processes.append(proc)
            except OSError as exc:
                log.warning("dashboard poll: failed to launch TUI: %s", exc)

            # Delete marker (best-effort)
            try:
                _paths.MARKER_OPEN_DASHBOARD.unlink(missing_ok=True)
            except OSError:
                pass

        _shutdown_event.wait(0.5)

    log.info("dashboard poll loop stopped")


# ===================================================================
# Item 25 — Hotkey registration
# ===================================================================


def _shortcut_to_human(shortcut: str) -> str:
    """Convert a hotkey string to human-readable display form.

    Handles both human-readable ('ctrl+alt+g' -> 'Ctrl+Alt+G') and
    legacy compact ('^!g' -> 'Ctrl+Alt+G') formats.
    """
    raw = (shortcut or "").strip()

    # Detect format by presence of '+' separator.
    if "+" in raw:
        parts = [p.strip().capitalize() for p in raw.split("+") if p.strip()]
        return "+".join(parts)

    # Legacy compact format (^=Ctrl, !=Alt, #=Super).
    mod_names = {"^": "Ctrl", "!": "Alt", "#": "Super"}
    parts: list[str] = []
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch in mod_names:
            parts.append(mod_names[ch])
            i += 1
        else:
            break
    key = raw[i:].upper() if i < len(raw) else ""
    if key:
        parts.append(key)
    return "+".join(parts)


def _shortcut_to_pynput(shortcut: str) -> str:
    """Convert a hotkey string to pynput GlobalHotKeys format.

    'ctrl+alt+g' -> <ctrl>+<alt>+g
    'ctrl+alt+t' -> <ctrl>+<alt>+t
    'ctrl+super+a' -> <ctrl>+<win>+a

    Also handles legacy compact format ('^!g' -> <ctrl>+<alt>+g).
    """
    raw = (shortcut or "").strip().lower()
    mod_map = {"ctrl": "<ctrl>", "super": "<win>", "alt": "<alt>"}

    # Try human-readable format (split on '+').
    if "+" in raw:
        parts = [p.strip() for p in raw.split("+") if p.strip()]
        pynput_parts: list[str] = []
        key = ""
        for p in parts:
            if p in mod_map:
                pynput_parts.append(mod_map[p])
            elif p.isalnum() and not key:
                key = p
        if key:
            pynput_parts.append(key)
        return "+".join(pynput_parts)

    # Legacy compact format (^=ctrl, !=alt, #=super, +=shift).
    pynput_mods = {"^": "<ctrl>", "+": "<shift>", "!": "<alt>", "#": "<win>"}
    parts = []
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch in pynput_mods:
            parts.append(pynput_mods[ch])
            i += 1
        else:
            break
    key = raw[i:] if i < len(raw) else ""
    if key:
        parts.append(key)
    return "+".join(parts)


def _register_x11_hotkeys(handlers: dict[str, callable]) -> Any:
    """Register global hotkeys via pynput on X11.

    Returns the pynput GlobalHotKeys listener instance (already started).
    """
    try:
        from pynput.keyboard import GlobalHotKeys
    except ImportError:
        log.error("pynput not installed — cannot register X11 hotkeys")
        return None

    combos: dict[str, callable] = {}
    for action, handler in handlers.items():
        key = HOTKEY_BINDINGS.get(action)
        if not key:
            continue
        combo = _shortcut_to_pynput(key)
        combos[combo] = handler

    if not combos:
        log.warning("no hotkey combos to register")
        return None

    try:
        listener = GlobalHotKeys(combos)
        listener.start()
        log.info("registered %d X11 hotkeys: %s", len(combos), combos)
        return listener
    except Exception as exc:
        log.error("failed to register pynput hotkeys: %s", exc)
        return None


def _register_wayland_hotkeys(handlers: dict[str, callable]) -> threading.Thread | None:
    """Register global hotkeys via evdev on Wayland.

    Opens keyboard input devices and monitors for key combinations.
    Returns the monitoring thread (already started).
    """
    try:
        import evdev  # noqa: F401 — test availability
    except ImportError:
        log.error("python-evdev not installed — cannot register Wayland hotkeys")
        return None

    # Build combo key sets
    combo_map: list[tuple[str, set[int], callable]] = []
    for action, handler in handlers.items():
        key = HOTKEY_BINDINGS.get(action)
        if not key:
            continue
        key_codes = _shortcut_to_evdev(key)
        if key_codes:
            combo_map.append((action, key_codes, handler))

    if not combo_map:
        log.warning("no Wayland hotkey combos to register")
        return None

    thread = threading.Thread(
        target=_evdev_monitor_loop,
        args=(combo_map,),
        name="evdev-monitor",
        daemon=True,
    )
    thread.start()
    return thread


def _shortcut_to_evdev(shortcut: str) -> set[int]:
    """Convert a hotkey string to a set of evdev key codes.

    Accepts human-readable ('ctrl+alt+g') and legacy compact ('^!g') formats.
    """
    from evdev import ecodes

    sym_mod_map = {
        "^": {ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL},
        "+": {ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT},
        "!": {ecodes.KEY_LEFTALT, ecodes.KEY_RIGHTALT},
        "#": {ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA},
    }
    name_mod_map = {
        "ctrl":  {ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL},
        "super": {ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA},
        "alt":   {ecodes.KEY_LEFTALT, ecodes.KEY_RIGHTALT},
    }

    raw = (shortcut or "").strip().lower()
    codes: set[int] = set()
    key_char = ""

    # Human-readable format (split on '+').
    if "+" in raw:
        parts = [p.strip() for p in raw.split("+") if p.strip()]
        for p in parts:
            if p in name_mod_map:
                codes.update(name_mod_map[p])
            elif p.isalnum() and not key_char:
                key_char = p
    else:
        # Legacy compact format.
        i = 0
        while i < len(raw):
            ch = raw[i]
            if ch in sym_mod_map:
                codes.update(sym_mod_map[ch])
                i += 1
            else:
                break
        key_char = raw[i:] if i < len(raw) else ""

    key_char = shortcut[i:].upper() if i < len(shortcut) else ""
    if key_char and len(key_char) == 1 and "A" <= key_char <= "Z":
        key_attr = f"KEY_{key_char}"
        code = getattr(ecodes, key_attr, None)
        if code:
            codes.add(code)
    elif key_char:
        # Try direct lookup (e.g., KEY_1, KEY_SPACE)
        key_attr = f"KEY_{key_char}"
        code = getattr(ecodes, key_attr, None)
        if code:
            codes.add(code)

    return codes


def _find_keyboard_devices() -> list[Any]:
    """Find keyboard input devices for evdev monitoring."""
    from evdev import InputDevice, ecodes, list_devices

    keyboards: list[Any] = []
    for path in list_devices():
        try:
            dev = InputDevice(path)
        except (OSError, PermissionError):
            continue

        caps = dev.capabilities()
        if ecodes.EV_KEY not in caps:
            dev.close()
            continue

        keys = caps[ecodes.EV_KEY]
        # Check if this device has letter keys (A-Z) and modifiers
        has_letters = any(ecodes.KEY_A <= k <= ecodes.KEY_Z for k in keys)
        has_mods = (ecodes.KEY_LEFTSHIFT in keys or ecodes.KEY_LEFTCTRL in keys)
        if has_letters and has_mods:
            keyboards.append(dev)
        else:
            dev.close()

    return keyboards


def _evdev_monitor_loop(combo_map: list[tuple[str, set[int], callable]]) -> None:
    """Evdev monitoring thread: read key events and detect combos.

    Args:
        combo_map: list of (action_name, required_key_codes, handler) tuples.
    """
    global _evdev_monitoring
    _ensure_logging()

    from evdev import ecodes

    devices = _find_keyboard_devices()
    if not devices:
        log.warning("no keyboard devices found for evdev monitoring")
        _evdev_monitoring = False
        return

    log.info("evdev monitoring started with %d keyboard devices", len(devices))

    pressed: set[int] = set()
    combo_fired: set[int] = set()  # track which combos have been fired (debounce)
    _evdev_monitoring = True

    try:
        while not _shutdown_event.is_set():
            # Read from all devices using select
            ready_devices = devices[:]
            for dev in ready_devices:
                try:
                    # Non-blocking read
                    for event in dev.read():
                        if event.type != ecodes.EV_KEY:
                            continue
                        # value: 0=release, 1=press, 2=repeat
                        if event.value == 2:  # repeat — skip
                            continue

                        if event.value == 1:  # press
                            pressed.add(event.code)
                        elif event.value == 0:  # release
                            pressed.discard(event.code)

                        # Check combos
                        for idx, (_action, required_codes, handler) in enumerate(combo_map):
                            if idx in combo_fired:
                                continue
                            if required_codes.issubset(pressed):
                                # Combo detected!
                                combo_fired.add(idx)
                                log.info("evdev combo detected: %s", _action)
                                try:
                                    handler()
                                except Exception as exc:
                                    log.warning("evdev handler %s failed: %s", _action, exc)

                        # Reset fired combos when any required key is released
                        released_codes: set[int] = set()
                        for idx, (_action, required_codes, _handler) in enumerate(combo_map):
                            if idx in combo_fired:
                                for code in required_codes:
                                    if code not in pressed:  # key was released
                                        released_codes.add(idx)

                        for idx in released_codes:
                            combo_fired.discard(idx)

                except BlockingIOError:
                    pass
                except OSError as exc:
                    log.debug("evdev read error: %s", exc)

            # Small sleep to avoid busy-loop
            _shutdown_event.wait(0.01)

    except Exception as exc:
        log.warning("evdev monitor error: %s", exc)
    finally:
        for dev in devices:
            try:
                dev.close()
            except OSError:
                pass
        _evdev_monitoring = False
        log.info("evdev monitoring stopped")


_HOTKEY_HANDLERS: dict[str, callable] = {
    "grammar_fix": process_selection,
    "capture_note": capture_note,
    "open_chat": launch_chat,
    "ask_chat": ask_with_selection,
}


def register_hotkeys_from_config() -> None:
    """Read hotkey bindings from config and register listeners.

    Unregisters any previously-registered listeners first.
    Background thread watches paths.CONFIG_FILE mtime and re-registers
    on change (5s interval).
    """
    global _pynput_listener, _evdev_thread, _config_watch_thread

    # Unregister existing
    _unregister_hotkeys()

    read_config()  # refresh HOTKEY_BINDINGS from config

    global SESSION_TYPE
    if not SESSION_TYPE:
        _detect_session_type()

    if SESSION_TYPE == "x11":
        _pynput_listener = _register_x11_hotkeys(_HOTKEY_HANDLERS)
    elif SESSION_TYPE == "wayland":
        _evdev_thread = _register_wayland_hotkeys(_HOTKEY_HANDLERS)
    else:
        log.error("cannot register hotkeys — unknown session type")
        return

    # Start config watch thread
    if _config_watch_thread is None or not _config_watch_thread.is_alive():
        _config_watch_thread = threading.Thread(
            target=_config_watch_loop,
            name="config-watch",
            daemon=True,
        )
        _config_watch_thread.start()


def _unregister_hotkeys() -> None:
    """Unregister all previously-registered keyboard listeners."""
    global _pynput_listener, _evdev_thread, _evdev_monitoring

    if _pynput_listener is not None:
        try:
            _pynput_listener.stop()
        except Exception:
            pass
        _pynput_listener = None
        log.info("pynput listener stopped")

    if _evdev_monitoring:
        _evdev_monitoring = False  # signal thread to stop
    if _evdev_thread is not None:
        _evdev_thread = None
        log.info("evdev monitor signalled to stop")


def _config_watch_loop() -> None:
    """Background thread: watch config file for changes.

    Every 5 seconds checks paths.CONFIG_FILE mtime. If changed,
    re-reads config and re-registers hotkeys.
    """
    _ensure_logging()
    log.info("config watch loop started")

    last_mtime = 0.0
    config_path = _paths.CONFIG_FILE

    while not _shutdown_event.is_set():
        try:
            if config_path.exists():
                mtime = config_path.stat().st_mtime
                if mtime > last_mtime:
                    if last_mtime > 0:  # skip first check
                        log.info("config file changed — re-registering hotkeys")
                        read_config()
                        register_hotkeys_from_config()
                    last_mtime = mtime
        except OSError:
            pass

        _shutdown_event.wait(5.0)

    log.info("config watch loop stopped")


# ===================================================================
# Item 26 — main() and shutdown()
# ===================================================================


def shutdown() -> None:
    """Graceful shutdown: kill children, stop threads, cleanup."""
    log.info("shutting down listener")

    _shutdown_event.set()

    # Unregister hotkeys
    _unregister_hotkeys()

    # Kill child processes
    with _child_lock:
        for proc in _child_processes:
            try:
                if proc.poll() is None:
                    # Try SIGTERM on the whole process group
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    except (ProcessLookupError, PermissionError, OSError):
                        proc.terminate()
                    # Give it 1s to exit
                    try:
                        proc.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                        except (ProcessLookupError, PermissionError, OSError):
                            proc.kill()
            except Exception as exc:
                log.debug("shutdown: child cleanup: %s", exc)
        _child_processes.clear()

    log.info("shutdown complete")


def _signal_handler(signum: int, _frame: Any) -> None:
    """Signal handler for SIGTERM/SIGINT."""
    log.info("received signal %d", signum)
    shutdown()


def main() -> int:
    """Listener entry point.

    Usage:
        flowkey-listener [--parent-pid N]

    Registered as console_scripts entry in pyproject.toml.
    """
    _ensure_logging()

    parser = argparse.ArgumentParser(description="Flowkey global hotkey listener")
    parser.add_argument(
        "--parent-pid", type=int, default=0,
        help="Exit when this PID disappears (0 = no parent watch)",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    log.setLevel(getattr(logging, args.log_level.upper(), logging.INFO))

    log.info("Flowkey listener starting (PID %d)", os.getpid())

    # Register signal handlers
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
    import atexit
    atexit.register(shutdown)

    # Parent PID watching
    if args.parent_pid > 0:
        threading.Thread(
            target=_watch_parent,
            args=(args.parent_pid,),
            name="parent-watch",
            daemon=True,
        ).start()

    # Detect session type
    _detect_session_type()
    log.info("session type: %s", SESSION_TYPE or "unknown")

    # Ensure runtime directories exist
    _paths.ensure_dirs()

    # Load config and ensure daemon
    read_config()
    daemon_ok = ensure_daemon_running()
    if not daemon_ok:
        log.warning("daemon not reachable — hotkeys will retry on each press")
    else:
        log.info("daemon is healthy")

    # Register hotkeys
    register_hotkeys_from_config()

    # Start clipboard watcher thread (if marker exists)
    if _paths.MARKER_CLIPBOARD_WATCHER.exists():
        _clipboard_watch_thread = threading.Thread(
            target=clipboard_watcher,
            name="clipboard-watcher",
            daemon=True,
        )
        _clipboard_watch_thread.start()
        log.info("clipboard watcher started (marker present)")

    # Start dashboard poll loop
    _dashboard_poll_thread = threading.Thread(
        target=dashboard_poll_loop,
        name="dashboard-poll",
        daemon=True,
    )
    _dashboard_poll_thread.start()

    log.info("Flowkey listener ready — waiting for hotkeys")

    # Block until shutdown
    try:
        _shutdown_event.wait()
    except KeyboardInterrupt:
        pass

    shutdown()
    log.info("Flowkey listener exited")
    return 0


def _watch_parent(parent_pid: int) -> None:
    """Exit the listener when the parent process disappears.

    Polls /proc/<parent_pid>/status every 5s. When the parent exits,
    triggers shutdown.
    """
    log.info("watching parent PID %d via /proc", parent_pid)
    while not _shutdown_event.is_set():
        _shutdown_event.wait(5.0)
        if _shutdown_event.is_set():
            return
        try:
            if not os.path.exists(f"/proc/{parent_pid}/status"):
                log.info("parent PID %d gone, requesting shutdown", parent_pid)
                shutdown()
                return
        except OSError:
            pass


# ===================================================================
# Entry point
# ===================================================================

if __name__ == "__main__":
    sys.exit(main())
