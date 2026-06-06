"""Flowkey long-running HTTP action daemon (Linux).

Listens on http://127.0.0.1:52650. The dashboard and listener post JSON to
/action/<name> and get JSON back. Imports `grammar_fix` once at startup so
per-call cost is just IPC + the action body — typically 5–20 ms.

Lifecycle:
- Bound port = single-instance lock. Second launch exits with code 0 if a
  prior daemon is already healthy.
- `--parent-pid N` makes the daemon exit when its parent process exits.
  Without it, the daemon runs until killed.
- Logs to the runtime logs directory resolved by `paths.LOGS_DIR`.

Stdlib only. No external dependencies.
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import paths as _paths

HERE = Path(__file__).resolve().parent

# Make `import grammar_fix` work whether we're run as a script or via entry point.
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import config  # noqa: E402
import notify  # noqa: E402
import grammar_fix  # noqa: E402


def _spawn_logged(name: str, argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Wrapper around subprocess.run that logs every spawn at INFO level."""
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    kwargs.setdefault("check", False)
    start = time.time()
    try:
        result = subprocess.run(argv, **kwargs)
        elapsed_ms = (time.time() - start) * 1000.0
        log.info("spawn name=%s argv=%s exit=%d elapsed_ms=%.1f",
                 name, argv[0:1] + ["…"] if len(argv) > 2 else argv,
                 result.returncode, elapsed_ms)
        return result
    except Exception as e:
        elapsed_ms = (time.time() - start) * 1000.0
        log.warning("spawn name=%s argv=%s FAILED elapsed_ms=%.1f error=%s",
                    name, argv[0], elapsed_ms, e)
        raise


def _popen_logged(name: str, argv: list[str], **kwargs) -> subprocess.Popen:
    """Start a long-lived child without waiting for it to exit."""
    kwargs.setdefault("stdin", subprocess.DEVNULL)
    kwargs.setdefault("stdout", subprocess.DEVNULL)
    kwargs.setdefault("stderr", subprocess.DEVNULL)
    proc = subprocess.Popen(argv, **kwargs)
    log.info("spawn name=%s argv=%s pid=%s", name,
             argv[0:1] + ["..."] if len(argv) > 2 else argv, proc.pid)
    return proc


def _chat_launch_argv() -> list[str]:
    parent_arg = ["--parent-pid", str(os.getpid())]
    if getattr(sys, "frozen", False):
        chat_bin = Path(sys.executable).with_name("ffp-chat")
        if chat_bin.exists():
            return [str(chat_bin), *parent_arg]
    return [sys.executable, str(HERE / "chat_popup.py"), *parent_arg]


HOST = "127.0.0.1"
DEFAULT_PORT = 52650
# API_VERSION doubles as the required POST header value (X-FFP-API). The local
# clients send the matching literal. CONTRACT: bump both together — a daemon/client
# version mismatch is rejected with 403 by design.
API_VERSION = "1"
_MAX_BODY_BYTES = 8 * 1024 * 1024  # reject oversized POST bodies (local DoS guard)

_paths.ensure_dirs()
LOG_DIR = _paths.LOGS_DIR  # centralized via paths.py

log = logging.getLogger("flowkey.daemon")


# ---------- Action dispatch ----------------------------------------------------------
#
# Each entry maps an action name → callable. Read-only callables return data;
# mutating ones return a status string. All exceptions surface as 5xx JSON errors.
# The callable signature is (args_dict) -> Any. args_dict comes from the request
# JSON body's `"args"` field (or {}).


def _ok(result: Any, elapsed_ms: float) -> dict:
    return {"ok": True, "result": result, "error": None, "elapsed_ms": round(elapsed_ms, 2)}


def _err(message: str, elapsed_ms: float) -> dict:
    return {"ok": False, "result": None, "error": message, "elapsed_ms": round(elapsed_ms, 2)}


def _act_status(_args: dict) -> str:
    return grammar_fix.server_status()


# ---- Autostart toggle (XDG autostart .desktop file) ---------------------------

_AUTOSTART_DESKTOP = "ffp-listener.desktop"
_AUTOSTART_DIR = Path(os.path.expanduser("~/.config/autostart"))


def _autostart_desktop_path() -> str:
    """Path to the XDG autostart .desktop file for the listener."""
    return str(_AUTOSTART_DIR / _AUTOSTART_DESKTOP)


def _act_get_autostart_state(_args: dict) -> dict:
    """Report whether the XDG autostart .desktop file exists."""
    path = _autostart_desktop_path()
    exists = os.path.isfile(path)
    return {"enabled": exists, "supported": True, "value": path if exists else ""}


def _act_set_autostart(args: dict) -> dict:
    """Create or remove the XDG autostart .desktop entry.

    args.enabled (bool) — True to add, False to remove.
    """
    enabled = bool(args.get("enabled"))
    path = _autostart_desktop_path()

    if enabled:
        desktop_content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Flowkey Listener\n"
            "Comment=Flowkey global hotkey listener\n"
            f"Exec=ffp-listener --parent-pid {os.getpid()}\n"
            "Terminal=false\n"
            "X-GNOME-Autostart-enabled=true\n"
        )
        try:
            _AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
            with open(path, "w") as f:
                f.write(desktop_content)
            return {"ok": True, "enabled": True, "value": path}
        except OSError as exc:
            return {"ok": False, "error": f"Could not write autostart file: {exc}"}

    # Disable: remove the file if present.
    try:
        if os.path.isfile(path):
            os.unlink(path)
    except OSError as exc:
        return {"ok": False, "error": f"Could not remove autostart file: {exc}"}
    return {"ok": True, "enabled": False, "value": ""}


def _act_start(_args: dict) -> str:
    return grammar_fix.start_flm_server(force_restart=False)


def _act_warmup(_args: dict) -> str:
    grammar_fix.start_flm_server(force_restart=False)
    grammar_fix._warmup_request(grammar_fix.FLM_MODEL)
    return "warmed_up"


def _act_restart(_args: dict) -> str:
    return grammar_fix.start_flm_server(force_restart=True)


def _act_stop(_args: dict) -> str:
    return "stopped" if grammar_fix.stop_flm_server(force=True) else "not_running"


def _act_performance(_args: dict) -> str:
    return grammar_fix.get_current_performance_mode()


def _act_toggle_performance(_args: dict) -> str:
    return grammar_fix.toggle_performance_mode()


def _act_set_perf_balanced(_args: dict) -> str:
    return grammar_fix.set_performance_mode("balanced")


def _act_set_perf_max(_args: dict) -> str:
    return grammar_fix.set_performance_mode("max")


def _act_history_text_status(_args: dict) -> str:
    return grammar_fix.get_history_text_mode()


def _act_toggle_history_text(_args: dict) -> str:
    return grammar_fix.toggle_history_text_mode()


def _act_set_history_visible(_args: dict) -> str:
    return grammar_fix.set_history_text_mode("visible")


def _act_set_history_redacted(_args: dict) -> str:
    return grammar_fix.set_history_text_mode("redacted")


def _act_tone_preset(_args: dict) -> str:
    return grammar_fix.get_tone_preset()


def _act_cycle_tone_preset(_args: dict) -> str:
    return grammar_fix.cycle_tone_preset()


def _act_set_tone(args: dict) -> str:
    preset = str(args.get("preset", "")).strip().lower()
    if preset not in {"formal", "casual", "friendly"}:
        raise ValueError(f"unknown tone preset: {preset!r}")
    cfg = grammar_fix.load_config()
    cfg.setdefault("modes", {}).setdefault("tone", {})["preset"] = preset
    grammar_fix.save_config(cfg)
    return preset


def _act_stats(_args: dict) -> dict:
    return grammar_fix.compute_usage_stats()


def _act_dashboard_data(_args: dict) -> dict:
    return grammar_fix.compute_dashboard_data()


def _act_config_snapshot(_args: dict) -> dict:
    return grammar_fix.build_config_snapshot()


def _act_models_list(_args: dict) -> dict:
    return grammar_fix.list_flm_models()


def _act_models_installed(_args: dict) -> dict:
    return grammar_fix._flm_list("installed")


def _act_models_not_installed(_args: dict) -> dict:
    return grammar_fix._flm_list("not-installed")


def _act_pull_model(args: dict) -> str:
    name = str(args.get("value", "")).strip()
    if not name:
        raise ValueError("pull_model requires args.value")
    try:
        import actions
        result = _spawn_logged(
            "flm.pull", ["flm", "pull", name],
            timeout=actions.PULL_MODEL_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        raise RuntimeError("flm CLI not found in PATH")
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        raise RuntimeError(f"flm pull failed (exit {result.returncode}):\n{output.strip()}")
    return output.strip() or f"pulled {name}"


def _act_remove_model(args: dict) -> str:
    name = str(args.get("value", "")).strip()
    if not name:
        raise ValueError("remove_model requires args.value")
    try:
        result = _spawn_logged("flm.remove", ["flm", "remove", name], timeout=60)
    except FileNotFoundError:
        raise RuntimeError("flm CLI not found in PATH")
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        raise RuntimeError(f"flm remove failed (exit {result.returncode}):\n{output.strip()}")
    return output.strip() or f"removed {name}"


def _act_apply_config_patch(args: dict) -> str:
    patch = args.get("patch")
    if patch is None:
        file_arg = args.get("file")
        if not file_arg:
            raise ValueError("apply_config_patch requires args.patch (dict) or args.file (path)")
        patch = json.loads(
            config.validate_patch_file(Path(file_arg)).read_text(encoding="utf-8")
        )
    if not isinstance(patch, dict):
        raise ValueError("patch must be a JSON object")
    return grammar_fix.apply_config_patch(patch)


def _act_doctor(_args: dict) -> str:
    return grammar_fix.run_doctor()


def _act_version(_args: dict) -> str:
    return grammar_fix.APP_VERSION


def _act_update_check(_args: dict) -> dict:
    return grammar_fix.check_for_update()


def _act_update_apply(_args: dict) -> str:
    return grammar_fix.apply_update()


def _act_flm_update_check(args: dict) -> dict:
    """Compare the installed FastFlowLM (flm) version with the latest GitHub
    release. Cached ~24h in data/; args.force bypasses the cache."""
    import flm_server
    cache = _paths.DATA_DIR / "flm_update_cache.json"
    return flm_server.check_flm_update(
        cache_path=cache,
        force=bool(args.get("force")),
        cache_only=bool(args.get("cache_only")),
    )


def _act_bench_start(args: dict) -> dict:
    """Kick off `flm bench <model>` on a background thread (10-20 min)."""
    import benchmark
    import flm_server
    model = str(args.get("model") or args.get("value") or "").strip()
    if not model:
        return {"ok": False, "error": "bench_start requires args.model"}
    return benchmark.start_benchmark(
        model,
        _paths.DATA_DIR / "benchmarks",
        flm_version=flm_server.flm_version(),
        stop_serve=lambda: grammar_fix.stop_flm_server(force=True),
        start_serve=lambda: grammar_fix.start_flm_server(force_restart=False),
    )


def _act_pull_start(args: dict) -> dict:
    """Start an async `flm pull <model>` on a background thread (non-blocking)."""
    import pull
    model = str(args.get("model") or args.get("value") or "").strip()
    if not model:
        return {"ok": False, "error": "pull_start requires args.model"}
    return pull.start_pull(model)


def _act_pull_status(_args: dict) -> dict:
    import pull
    return pull.status()


def _act_note_search(args: dict) -> dict:
    """Search the notes vault. args.query (str), args.limit (int, default 5)."""
    import notes
    query = str(args.get("query") or args.get("value") or "").strip()
    try:
        limit = int(args.get("limit") or 5)
    except (TypeError, ValueError):
        limit = 5
    return notes.search_notes(query, limit)


def _act_bench_status(_args: dict) -> dict:
    import benchmark
    return benchmark.status()


def _act_bench_history(_args: dict) -> dict:
    import benchmark
    return benchmark.history(_paths.DATA_DIR / "benchmarks")


def _xml_escape(s: str) -> str:
    """Backward-compat alias for tests; implementation lives in notify."""
    return notify.xml_escape(s)


def _show_toast_async(title: str, message: str) -> None:
    notify.show_toast_async(title, message)


def _act_save_note(args: dict) -> dict:
    """Capture a note. Returns {note_id, path, is_url_only}."""
    import notes
    text = str(args.get("text") or "")
    source_app = str(args.get("source_app") or "")
    url = str(args.get("url") or "")
    return notes.capture_note(text=text, source_app=source_app, url=url)


def _act_notify(args: dict) -> str:
    title = str(args.get("title") or "").strip() or "Flowkey"
    message = str(args.get("message") or "").strip()
    if not message:
        return "no-op (empty message)"
    _show_toast_async(title, message)
    return "queued"


def _act_open_dashboard(_args: dict) -> str:
    """Signal the front-end to open the dashboard (marker file)."""
    try:
        _paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
        _paths.MARKER_OPEN_DASHBOARD.write_text("1\n", encoding="utf-8")
    except OSError as exc:
        log.warning("open_dashboard marker write failed: %s", exc)
        raise RuntimeError(f"could not write dashboard marker: {exc}") from exc
    return "queued"


def _act_shutdown(_args: dict) -> str:
    threading.Timer(0.05, lambda: _shutdown_event.set()).start()
    return "shutting_down"


def _read_chat_ingest_nonce() -> str:
    """Read the chat instance's ingest nonce (empty when chat is not running)."""
    nonce_path = _paths.DATA_DIR / ".chat_ingest_nonce"
    try:
        if nonce_path.exists():
            return nonce_path.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return ""


def _build_chat_ingest_payload(text: str, source_app: str) -> bytes:
    return json.dumps({
        "type": "ingest",
        "text": text,
        "source_app": source_app,
        "nonce": _read_chat_ingest_nonce(),
    }, ensure_ascii=False).encode("utf-8")


def _act_chat_reload(_args: dict) -> str:
    """Tell a running chat popup to reload config (model/base_url). Best-effort."""
    import socket as _sock

    chat_port = 52640
    try:
        with _sock.create_connection(("127.0.0.1", chat_port), timeout=0.5) as c:
            c.sendall(b"RELOAD\n")
        return "ok"
    except OSError:
        return "chat not running"


def _act_chat_restart(_args: dict) -> str:
    """Quit a running chat popup so the next open loads fresh config/model."""
    import socket as _sock

    chat_port = 52640
    try:
        with _sock.create_connection(("127.0.0.1", chat_port), timeout=0.5) as c:
            c.sendall(b"QUIT\n")
        return "ok"
    except OSError:
        return "chat not running"


def _act_chat_send_selection(args: dict) -> dict:
    """Send a selection to the chat single-instance port (52640) as an
    ingest payload. If chat isn't running, spawn it first and retry."""
    import socket as _sock

    text = str(args.get("text") or "")
    source_app = str(args.get("source_app") or "")
    if not text:
        return {"ok": False, "error": "empty selection"}

    chat_port = 52640  # single_instance_port

    def _try_send() -> bytes | None:
        payload = _build_chat_ingest_payload(text, source_app)
        try:
            with _sock.create_connection(("127.0.0.1", chat_port), timeout=0.5) as c:
                c.sendall(payload + b"\n")
            return payload
        except OSError:
            return None

    sent = _try_send()
    if sent is not None:
        return {"ok": True, "spawned": False, "bytes": len(sent)}

    # Chat not running — spawn it and wait briefly for the listener to bind.
    try:
        _popen_logged("chat_popup_for_ingest", _chat_launch_argv(), cwd=str(HERE))
    except Exception as e:
        return {"ok": False, "error": f"chat spawn failed: {e}"}

    for _ in range(20):  # up to ~2s
        time.sleep(0.1)
        sent = _try_send()
        if sent is not None:
            return {"ok": True, "spawned": True, "bytes": len(sent)}

    return {"ok": False, "error": "chat did not accept ingest after spawn"}


ACTIONS: dict[str, Callable[[dict], Any]] = {
    "status": _act_status,
    "start": _act_start,
    "warmup": _act_warmup,
    "restart": _act_restart,
    "stop": _act_stop,
    "performance": _act_performance,
    "toggle_performance": _act_toggle_performance,
    "set_perf_balanced": _act_set_perf_balanced,
    "set_perf_max": _act_set_perf_max,
    "history_text_status": _act_history_text_status,
    "toggle_history_text": _act_toggle_history_text,
    "set_history_visible": _act_set_history_visible,
    "set_history_redacted": _act_set_history_redacted,
    "tone_preset": _act_tone_preset,
    "cycle_tone_preset": _act_cycle_tone_preset,
    "set_tone": _act_set_tone,
    "set_tone_formal": lambda a: _act_set_tone({"preset": "formal"}),
    "set_tone_casual": lambda a: _act_set_tone({"preset": "casual"}),
    "set_tone_friendly": lambda a: _act_set_tone({"preset": "friendly"}),
    "stats": _act_stats,
    "dashboard_data": _act_dashboard_data,
    "config_snapshot": _act_config_snapshot,
    "models_list": _act_models_list,
    "models_installed": _act_models_installed,
    "models_not_installed": _act_models_not_installed,
    "pull_model": _act_pull_model,
    "remove_model": _act_remove_model,
    "apply_config_patch": _act_apply_config_patch,
    "doctor": _act_doctor,
    "version": _act_version,
    "update_check": _act_update_check,
    "update_apply": _act_update_apply,
    "flm_update_check": _act_flm_update_check,
    "bench_start": _act_bench_start,
    "bench_status": _act_bench_status,
    "bench_history": _act_bench_history,
    "note_search": _act_note_search,
    "pull_start": _act_pull_start,
    "pull_status": _act_pull_status,
    "notify": _act_notify,
    "save_note": _act_save_note,
    "chat_send_selection": _act_chat_send_selection,
    "chat_reload": _act_chat_reload,
    "chat_restart": _act_chat_restart,
    "get_autostart_state": _act_get_autostart_state,
    "set_autostart": _act_set_autostart,
    "open_dashboard": _act_open_dashboard,
    "shutdown": _act_shutdown,
}

# Mutating actions get a global lock so concurrent writes can't race the config file.
_write_lock = threading.Lock()
_WRITE_ACTIONS = {
    "start", "warmup", "restart", "stop",
    "toggle_performance", "set_perf_balanced", "set_perf_max",
    "toggle_history_text", "set_history_visible", "set_history_redacted",
    "cycle_tone_preset", "set_tone", "set_tone_formal", "set_tone_casual", "set_tone_friendly",
    "pull_model", "remove_model", "apply_config_patch", "update_apply",
    "set_autostart", "bench_start", "pull_start",
}

_shutdown_event = threading.Event()
_started_at = time.time()


# ---------- HTTP layer ---------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = f"FlowkeyDaemon/{API_VERSION}"

    def log_message(self, fmt: str, *args: Any) -> None:
        log.info("HTTP %s", fmt % args)

    def _send_json(self, status: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-FFP-API", API_VERSION)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            uptime = round(time.time() - _started_at, 1)
            self._send_json(200, {
                "ok": True,
                "version": grammar_fix.APP_VERSION,
                "api": API_VERSION,
                "uptime_seconds": uptime,
                "actions": sorted(ACTIONS.keys()),
            })
            return
        self._send_json(404, {"ok": False, "error": f"GET {self.path} not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.headers.get("X-FFP-API") != API_VERSION:
            self._send_json(403, _err("missing or invalid X-FFP-API header", 0.0))
            return
        if not self.path.startswith("/action/"):
            self._send_json(404, {"ok": False, "error": f"POST {self.path} not found"})
            return
        action_name = self.path[len("/action/"):].split("?", 1)[0]
        handler = ACTIONS.get(action_name)
        if handler is None:
            self._send_json(404, _err(f"unknown action: {action_name}", 0.0))
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send_json(400, _err("invalid Content-Length header", 0.0))
            return
        if length < 0 or length > _MAX_BODY_BYTES:
            self._send_json(413, _err(f"body too large ({length} bytes)", 0.0))
            return
        raw_body = self.rfile.read(length) if length > 0 else b""
        try:
            if raw_body.startswith(b"\xef\xbb\xbf"):  # UTF-8 BOM
                raw_body = raw_body[3:]
            payload = json.loads(raw_body.decode("utf-8"), strict=False) if raw_body else {}
        except UnicodeDecodeError as e:
            log.warning("action=%s utf8_decode_failed bytes=%r", action_name, raw_body[:80])
            self._send_json(400, _err(f"body not UTF-8: {e}", 0.0))
            return
        except Exception as e:
            log.warning("action=%s json_parse_failed body=%r", action_name, raw_body[:200])
            self._send_json(400, _err(f"invalid JSON body: {e}", 0.0))
            return
        if not isinstance(payload, dict):
            self._send_json(400, _err("JSON body must be an object", 0.0))
            return
        args = payload.get("args") or {}
        if not isinstance(args, dict):
            self._send_json(400, _err("JSON args must be an object", 0.0))
            return

        start = time.time()
        try:
            if action_name in _WRITE_ACTIONS:
                with _write_lock:
                    result = handler(args)
            else:
                result = handler(args)
            elapsed = (time.time() - start) * 1000.0
            log.info("action=%s status=ok elapsed_ms=%.1f", action_name, elapsed)
            self._send_json(200, _ok(result, elapsed))
        except Exception as e:
            elapsed = (time.time() - start) * 1000.0
            log.warning("action=%s status=error elapsed_ms=%.1f error=%s", action_name, elapsed, e)
            log.debug("traceback:\n%s", traceback.format_exc())
            self._send_json(500, _err(str(e), elapsed))


# ---------- Lifecycle ---------------------------------------------------------------

def _is_port_taken(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.3)
    try:
        s.connect((HOST, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _watch_parent(parent_pid: int) -> None:
    """Exit the daemon when the parent process disappears.

    Polls /proc/<parent_pid>/status every 5s. When the parent exits the
    proc entry disappears and the daemon shuts itself down.
    """
    log.info("watching parent PID %d via /proc", parent_pid)
    while not _shutdown_event.is_set():
        _shutdown_event.wait(5.0)
        if _shutdown_event.is_set():
            return
        if not os.path.exists(f"/proc/{parent_pid}/status"):
            log.info("parent PID %d gone, requesting shutdown", parent_pid)
            _shutdown_event.set()
            return


def _setup_logging(log_level: str) -> None:
    level = getattr(logging, log_level.upper(), logging.INFO)

    parent = logging.getLogger("ffp")
    parent.setLevel(level)
    if parent.handlers:
        return

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")

    file_handler = logging.handlers.TimedRotatingFileHandler(
        LOG_DIR / "daemon.log", when="midnight", backupCount=7, encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    parent.addHandler(file_handler)

    if sys.stderr and sys.stderr.isatty():
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(fmt)
        parent.addHandler(stream_handler)


def main() -> int:
    parser = argparse.ArgumentParser(description="Flowkey action daemon")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--parent-pid", type=int, default=0,
                        help="exit when this PID disappears (0 = no parent watch)")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    _setup_logging(args.log_level)

    if _is_port_taken(args.port):
        log.info("port %d already in use; assuming another daemon is healthy and exiting", args.port)
        return 0

    if args.parent_pid > 0:
        threading.Thread(target=_watch_parent, args=(args.parent_pid,), daemon=True).start()

    server = ThreadingHTTPServer((HOST, args.port), Handler)
    server.timeout = 1.0

    log.info("Flowkey daemon listening on http://%s:%d (version %s)",
             HOST, args.port, grammar_fix.APP_VERSION)

    try:
        while not _shutdown_event.is_set():
            server.handle_request()
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt; shutting down")
    finally:
        server.server_close()
        log.info("daemon stopped (uptime %.1fs)", time.time() - _started_at)
    return 0


if __name__ == "__main__":
    sys.exit(main())
