"""System tray indicator for Flowkey (Linux).

Uses pystray (X11) or dasbus StatusNotifierItem (Wayland).
Falls back gracefully if neither is available.

Menu structure:
  Open TUI        → launches flowkey-tui
  Server ───────── Status / Start / Stop / Warmup
  Performance ──── Balanced / Max
  ───────────────
  Exit

Config editing, history, notes, and benchmark are in the TUI only.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

import loopback_http
from config import PowerMode

log = logging.getLogger("flowkey.tray")

HERE = Path(__file__).resolve().parent
ICON_PATH = HERE / "assets" / "flowkey.png"

_power_mode: str = ""
_x11_icon: object | None = None  # set by _run_x11 after icon creation


def _daemon_action(action: str, notify_label: str, *, update_menu: bool = False) -> None:
    """POST *action* to the daemon, notify with result, optionally refresh tray menu."""
    global _power_mode
    resp = loopback_http.daemon_post(action)
    result = resp.get("result") or action.removeprefix("set_power_")
    _notify(notify_label, str(result)[:120] if isinstance(result, str) else str(result))
    if update_menu:
        resp2 = loopback_http.daemon_post("power_mode")
        _power_mode = str(resp2.get("result") or PowerMode.BALANCED.value).strip().lower()
        icon = _x11_icon
        if icon is not None:
            import pystray as _ps  # noqa: F811
            icon.menu = _build_x11_menu()  # type: ignore[union-attr]
            icon.update_menu()  # type: ignore[union-attr]


def _build_x11_menu():
    """Build the pystray menu tree using current _power_mode."""
    import pystray as _ps
    pm = _power_mode
    return _ps.Menu(
        _ps.MenuItem("Open TUI", _launch_tui, default=True),
        _ps.Menu.SEPARATOR,
        _ps.MenuItem(
            "Server",
            _ps.Menu(
                _ps.MenuItem("Status", lambda: _daemon_action("status", "Server status")),
                _ps.MenuItem("Start", lambda: _daemon_action("start", "Server")),
                _ps.MenuItem("Stop", lambda: _daemon_action("stop", "Server")),
                _ps.MenuItem("Warmup", lambda: _daemon_action("warmup", "Server")),
            ),
        ),
        _ps.MenuItem(
            "Power Mode",
            _ps.Menu(
                _ps.MenuItem("Power Saver", lambda: _daemon_action("set_power_powersaver", "Power Mode", update_menu=True),
                             checked=lambda: pm == PowerMode.POWERSAVER.value),
                _ps.MenuItem("Balanced", lambda: _daemon_action("set_power_balanced", "Power Mode", update_menu=True),
                             checked=lambda: pm == PowerMode.BALANCED.value),
                _ps.MenuItem("Performance", lambda: _daemon_action("set_power_performance", "Power Mode", update_menu=True),
                             checked=lambda: pm == PowerMode.PERFORMANCE.value),
                _ps.MenuItem("Turbo", lambda: _daemon_action("set_power_turbo", "Power Mode", update_menu=True),
                             checked=lambda: pm == PowerMode.TURBO.value),
            ),
        ),
        _ps.Menu.SEPARATOR,
        _ps.MenuItem("Exit", _on_exit),
    )


def _on_exit() -> None:
    os._exit(0)


def _refresh_power_mode() -> str:
    global _power_mode
    resp = loopback_http.daemon_post("power_mode")
    _power_mode = str(resp.get("result") or PowerMode.BALANCED.value).strip().lower()
    return _power_mode


# ---------------------------------------------------------------------------
# X11 tray (pystray)
# ---------------------------------------------------------------------------


def _run_x11() -> None:
    """Build and run the pystray icon on X11."""
    import pystray
    from PIL import Image

    icon_image = Image.open(ICON_PATH)
    _refresh_power_mode()

    icon = pystray.Icon(
        "flowkey",
        icon_image,
        title="Flowkey",
        menu=_build_x11_menu(),
    )
    global _x11_icon
    _x11_icon = icon
    icon.run()


# ---------------------------------------------------------------------------
# Wayland tray (dasbus StatusNotifierItem)
# ---------------------------------------------------------------------------


def _run_wayland() -> None:
    """Build and run the StatusNotifierItem via dasbus on Wayland."""
    import dasbus.connection

    bus = dasbus.connection.SessionMessageBus()

    try:
        # Register a StatusNotifierItem via org.kde.StatusNotifierItem
        # on the session bus. If that fails, we skip the tray on Wayland.
        _sni_register(bus, ICON_PATH.read_bytes())
    except Exception as exc:
        log.warning("Wayland StatusNotifierItem registration failed: %s", exc)
        log.warning("Flowkey tray not available on this Wayland compositor")
        sys.exit(0)


def _sni_register(bus, icon_data: bytes) -> None:
    """Register a StatusNotifierItem using the KDE protocol over dbus.

    This is a minimal implementation; full-featured tray on Wayland typically
    needs a dedicated library. If this fails, we degrade gracefully (exit).
    """
    # Placeholder: dasbus SNI registration is complex and compositor-specific.
    # For now, log and exit silently — the TUI remains accessible via hotkeys.
    log.info("Wayland StatusNotifierItem registration not yet fully implemented")
    sys.exit(0)


# ---------------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------------


def _notify(title: str, message: str) -> None:
    """Quick desktop notification via daemon or notify-send."""
    try:
        loopback_http.daemon_post(
            "notify",
            {"title": title[:64], "message": message[:256]},
            timeout=2.0,
        )
    except Exception as exc:
        log.debug("daemon notify failed, falling back to notify-send: %s", exc)
        # Fallback: direct notify-send
        try:
            subprocess.Popen(
                ["notify-send", title[:64], message[:256]],
                close_fds=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            print(f"[{title}] {message}", file=sys.stderr)


def _launch_tui() -> None:
    """Launch the Textual TUI as a subprocess."""
    tui_argv = _resolve_tui_argv()
    parent_arg = f"--parent-pid={os.getpid()}"
    tui_argv.append(parent_arg)
    try:
        subprocess.Popen(
            tui_argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError as exc:
        _notify("Flowkey", f"Failed to launch TUI: {exc}")


def _resolve_tui_argv() -> list[str]:
    """Resolve the flowkey-tui executable."""
    which = _which("flowkey-tui")
    if which:
        return [which]
    here = Path(__file__).resolve().parent
    tui_script = here / "tui" / "app.py"
    if tui_script.exists():
        return [sys.executable, str(tui_script)]
    return ["flowkey-tui"]


def _which(name: str) -> str | None:
    """Find executable in PATH."""
    path = os.environ.get("PATH", "")
    for directory in path.split(os.pathsep):
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _detect_session_type() -> str | None:
    """Detect X11 vs Wayland.

    Priority:
      1. $XDG_SESSION_TYPE env var
      2. $WAYLAND_DISPLAY env var (wayland-only)
      3. Import check for pystray + X11 libraries (X11 default)
    """
    session = os.environ.get("XDG_SESSION_TYPE", "").strip().lower()
    if session == "wayland":
        return "wayland"
    if session == "x11":
        return "x11"
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    # Default to X11 if pystray is available
    try:
        import pystray  # noqa: F401
        return "x11"
    except ImportError:
        pass
    return None


def main() -> int:
    """Tray entry point.

    Detects X11 vs Wayland and starts the appropriate tray implementation.
    Falls back gracefully if neither pystray (X11) nor dasbus (Wayland)
    is available.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    session = _detect_session_type()
    if not session:
        log.info("could not detect X11 or Wayland — tray not available")
        print("flowkey-tray: no supported desktop session detected", file=sys.stderr)
        return 1

    if session == "x11":
        try:
            import pystray  # noqa: F401
        except ImportError:
            log.info("pystray not installed — tray unavailable on X11")
            print("flowkey-tray: install pystray for X11 tray support", file=sys.stderr)
            return 1
        try:
            _run_x11()
        except Exception as exc:
            log.error("tray error: %s", exc)
            return 1
    elif session == "wayland":
        try:
            import dasbus  # noqa: F401
        except ImportError:
            log.info("dasbus not installed — tray unavailable on Wayland")
            return 0  # silent exit — tray unavailable, not an error
        try:
            _run_wayland()
        except Exception as exc:
            log.warning("Wayland tray error: %s", exc)
            return 0  # graceful degradation
    return 0


if __name__ == "__main__":
    sys.exit(main())
