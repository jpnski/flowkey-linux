"""Async `flm pull <model>` with progress (Linux).

Runs the pull on a daemon background thread, parses the latest percentage
from `flm pull`'s streamed stdout, and exposes it via status() for the
dashboard to poll. Single slot — one pull at a time.

On cancellation the partial model directory (``~/.config/flm/models/<repo>``)
is cleaned up so the model does not appear installed when it is unusable.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger("flowkey.pull")

_lock = threading.Lock()
_job: dict = {
    "state": "idle",       # idle | running | done | error | cancelled
    "model": "",
    "percent": 0.0,
    "message": "",
    "error": "",
    "started_at": 0.0,
    "finished_at": 0.0,
}
_thread: threading.Thread | None = None
_proc: subprocess.Popen | None = None
_PCT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")

# Where `flm pull` stores downloaded model data.
_FLM_MODELS_DIR = Path.home() / ".config" / "flm" / "models"


def _update(**fields) -> None:
    with _lock:
        _job.update(fields)


def status() -> dict:
    with _lock:
        return dict(_job)


def _default_runner(model: str, on_line: Callable[[str], None]) -> int:
    global _proc
    proc = subprocess.Popen(
        ["flm", "pull", model],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    _proc = proc
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            on_line(line)
        proc.wait()
        return proc.returncode
    finally:
        with _lock:
            was_cancelled = _job.get("state") == "cancelled"
        _proc = None
        if was_cancelled:
            return -1


def _resolve_model_dir_from_list(model: str) -> Path | None:
    """Resolve the model's data directory from ``flm list --json``.

    The directory name under ``~/.config/flm/models/`` matches the HuggingFace
    repo name — the last path component of the model's ``url`` field in the
    registry, stripped of any ``/resolve/…`` or ``/tree/…`` suffix.

    Returns ``None`` when the model isn't found in the registry, the CLI is
    missing, or the URL can't be parsed.
    """
    try:
        result = subprocess.run(
            ["flm", "list", "--json"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return None
        payload = json.loads(result.stdout)
    except (ValueError, OSError):
        return None

    for entry in payload.get("models") or []:
        name = str(entry.get("model") or "").strip()
        if name != model:
            continue
        url = str(entry.get("url") or "")
        # URL pattern: https://huggingface.co/{org}/{repo}[/resolve/...][/tree/...]
        if url and "huggingface.co/" in url:
            parts = url.split("huggingface.co/", 1)[1].split("/")
            if len(parts) >= 2:
                repo = parts[1]
                candidate = _FLM_MODELS_DIR / repo
                if candidate.exists():
                    return candidate
        break
    return None


def _cleanup_partial_model(model: str) -> None:
    """Remove partial model files after a cancelled pull.

    Two strategies in sequence:

    1. ``flm remove <model>`` — delegates name→directory translation to the
       FLM CLI itself.  This is the preferred path.
    2. Direct ``shutil.rmtree`` via the directory resolved from the model's
       HuggingFace URL in ``flm list --json``.

    If both fail the stale directory is logged (non-fatal — the system
    continues to work, and the user can manually ``flm remove <model>``).
    """
    log.info("cleaning up partial pull for %s", model)

    # ---- Strategy 1: flm remove (preferred) ----
    try:
        result = subprocess.run(
            ["flm", "remove", model],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            log.info("flm remove cleaned up %s", model)
            return
        log.warning(
            "flm remove failed for %s (exit %d): %s",
            model, result.returncode, (result.stderr or "").strip()[:200],
        )
    except Exception as exc:
        log.warning("flm remove exception for %s: %s", model, exc)

    # ---- Strategy 2: direct filesystem removal via URL-derived dir name ----
    try:
        model_dir = _resolve_model_dir_from_list(model)
        if model_dir is not None and model_dir.exists():
            shutil.rmtree(model_dir)
            log.info("direct rmtree removed %s for model %s", model_dir, model)
            return
        log.warning(
            "could not resolve model directory for %s "
            "(model_dir=%r exists=%s); stale dir may remain",
            model, model_dir, model_dir.exists() if model_dir else "N/A",
        )
    except Exception as exc:
        log.warning("direct cleanup failed for %s: %s", model, exc)


def start_pull(model: str, *,
               runner: Callable[[str, Callable[[str], None]], int] | None = None) -> dict:
    """Launch `flm pull <model>` on a background thread. Returns immediately;
    poll status(). Refuses a second concurrent pull."""
    global _thread
    model = str(model or "").strip()
    if not model:
        return {"ok": False, "error": "no model specified"}
    with _lock:
        if _job["state"] == "running":
            return {"ok": False, "error": "a pull is already running", "model": _job["model"]}
        _job.update({"state": "running", "model": model, "percent": 0.0,
                     "message": "starting…", "error": "", "started_at": time.time(), "finished_at": 0.0})
    run = runner or _default_runner

    def on_line(line: str) -> None:
        upd: dict = {}
        matches = _PCT_RE.findall(line)
        if matches:
            try:
                upd["percent"] = max(0.0, min(100.0, float(matches[-1])))
            except ValueError:
                pass
        stripped = line.strip()
        if stripped:
            upd["message"] = stripped[:160]
        if upd:
            _update(**upd)

    def worker() -> None:
        try:
            rc = run(model, on_line)
        except FileNotFoundError:
            log.warning("flm CLI not found in PATH while pulling %s", model)
            _update(state="error", error="flm CLI not found in PATH", finished_at=time.time())
            return
        except Exception as exc:
            log.exception("pull failed for %s", model)
            _update(state="error", error=str(exc), finished_at=time.time())
            return
        with _lock:
            was_cancelled = _job.get("state") == "cancelled"
        if was_cancelled:
            _update(message=f"{model} cancelled.", finished_at=time.time())
            _cleanup_partial_model(model)
        elif rc == 0:
            _update(state="done", percent=100.0, message=f"{model} downloaded.", finished_at=time.time())
        else:
            _update(state="error", error=f"flm pull exited with code {rc}", finished_at=time.time())

    _thread = threading.Thread(target=worker, name="flowkey-pull", daemon=True)
    _thread.start()
    return {"ok": True, "state": "running", "model": model}


def cancel_pull() -> dict:
    """Terminate the running `flm pull`, if any. Idempotent.

    Sets the job state to ``cancelled`` and sends SIGTERM to the flm
    subprocess. The worker thread detects the cancellation when the
    stdout iterator closes and leaves the state at ``cancelled``
    instead of clobbering it with ``error``.
    """
    global _proc
    with _lock:
        if _job["state"] != "running":
            return {
                "ok": False,
                "error": "no pull in progress",
                "state": _job["state"],
                "model": _job["model"],
            }
        _job["state"] = "cancelled"
        _job["message"] = "cancellation requested…"
        proc = _proc
    if proc is not None:
        try:
            proc.terminate()
        except Exception as exc:
            log.warning("terminate pull proc failed: %s", exc)
    return {"ok": True, "state": "cancelled", "model": _job["model"]}
