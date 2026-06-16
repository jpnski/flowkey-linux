"""ffchat engine — FLM server lifecycle, config CRUD, model management.

Called directly by the TUI (no daemon, no HTTP IPC).
"""

from __future__ import annotations

import json
import logging
import sys
import time
import urllib.request
from dataclasses import asdict
from pathlib import Path

import config
import flm_server
import llm_client
import paths as _paths
import version
from subprocess_util import run_flm

_is_selectable_chat_model = flm_server._is_selectable_chat_model

APP_VERSION = version.APP_VERSION

_paths.ensure_dirs()

TOOL_DIR = _paths.SCRIPTS_DIR
CONFIG_PATH = _paths.CONFIG_FILE
PID_PATH = _paths.FLM_PID_FILE

log = logging.getLogger("ffchat.engine")

# Module-level globals (refreshed from config on load/save).
CONFIG: config.FlowkeyConfig | None = None
FLM_BASE_URL: str = "http://127.0.0.1:52625"
FLM_MODEL: str = config.DEFAULT_CHAT_MODEL
FLM_TIMEOUT_SECONDS: int = 60
FLM_CFG: config.FlmApiConfig | None = None
SERVER_CFG: config.FlmServerConfig | None = None
SERVER_AUTO_START: bool = True
FLM_POWER_MODE: str = "balanced"
SERVING_STARTUP_TIMEOUT_S: int = 25
SERVING_EXTRA_ARGS: list[str] = []
SERVER_LOG_TO_FILE: bool = False
SERVER_LOG_FILE: str = "flm_server.log"

# Token usage accumulator.
_USAGE_ACC: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}


# ── Config ─────────────────────────────────────────────────────────────────

def load_config() -> config.FlowkeyConfig:
    return config.load_config(CONFIG_PATH)


def save_config(cfg: config.FlowkeyConfig) -> None:
    config.save_config(CONFIG_PATH, cfg)
    refresh_runtime_config()


def refresh_runtime_config() -> None:
    global CONFIG, FLM_BASE_URL, FLM_MODEL, FLM_TIMEOUT_SECONDS
    global FLM_CFG, SERVER_CFG, SERVER_AUTO_START
    global FLM_POWER_MODE, SERVING_STARTUP_TIMEOUT_S
    global SERVING_EXTRA_ARGS, SERVER_LOG_TO_FILE, SERVER_LOG_FILE

    CONFIG = load_config()
    try:
        FLM_BASE_URL = config.validate_flm_base_url(CONFIG.flm_api.url)
    except ValueError as exc:
        log.warning("invalid flm_base_url in config, using default: %s", exc)
        FLM_BASE_URL = "http://127.0.0.1:52625"
    FLM_MODEL = CONFIG.flm_server.model.strip()
    if FLM_MODEL and not _is_selectable_chat_model(FLM_MODEL):
        log.warning(
            "model %r in config is not a chat-selectable model; "
            "falling back to default %r",
            FLM_MODEL, config.DEFAULT_CHAT_MODEL,
        )
        FLM_MODEL = config.DEFAULT_CHAT_MODEL
    FLM_TIMEOUT_SECONDS = CONFIG.flm_api.timeout_s
    FLM_CFG = CONFIG.flm_api
    SERVER_CFG = CONFIG.flm_server
    SERVER_AUTO_START = CONFIG.flm_server.auto_start
    FLM_POWER_MODE = CONFIG.flm_server.power_mode.strip().lower()
    SERVING_STARTUP_TIMEOUT_S = CONFIG.flm_server.startup_timeout_s
    SERVING_EXTRA_ARGS = [str(a) for a in CONFIG.flm_server.extra_args]
    SERVER_LOG_TO_FILE = CONFIG.flm_server.log_to_file
    SERVER_LOG_FILE = CONFIG.flm_server.log_file


refresh_runtime_config()


# ── Server lifecycle ────────────────────────────────────────────────────────

def is_flm_server_reachable() -> bool:
    return flm_server.is_flm_server_reachable(FLM_BASE_URL)


def start_flm_server(force_restart: bool = False) -> str:
    settings = flm_server.FlmServerSettings(
        base_url=FLM_BASE_URL,
        model=FLM_MODEL,
        timeout_seconds=FLM_TIMEOUT_SECONDS,
        power_mode=FLM_POWER_MODE,
        startup_timeout_seconds=SERVING_STARTUP_TIMEOUT_S,
        extra_args=SERVING_EXTRA_ARGS,
        log_to_file=SERVER_LOG_TO_FILE,
        log_file=SERVER_LOG_FILE,
        pid_path=PID_PATH,
        logs_dir=_paths.LOGS_DIR,
    )
    return flm_server.start_flm_server(
        settings, call_flm_api,
        force_restart=force_restart,
        stop_callback=stop_flm_server,
    )


def stop_flm_server(force: bool = False) -> bool:
    settings = flm_server.FlmServerSettings(
        base_url=FLM_BASE_URL,
        model=FLM_MODEL,
        timeout_seconds=FLM_TIMEOUT_SECONDS,
        power_mode=FLM_POWER_MODE,
        startup_timeout_seconds=SERVING_STARTUP_TIMEOUT_S,
        extra_args=SERVING_EXTRA_ARGS,
        log_to_file=SERVER_LOG_TO_FILE,
        log_file=SERVER_LOG_FILE,
        pid_path=PID_PATH,
        logs_dir=_paths.LOGS_DIR,
    )
    return flm_server.stop_flm_server(settings, force=force)


def warmup_request(model: str | None = None) -> None:
    flm_server.warmup_request(model or FLM_MODEL, FLM_TIMEOUT_SECONDS, call_flm_api)


def server_status() -> str:
    """Return a single-line key=value summary."""
    reachable = is_flm_server_reachable()
    pid = flm_server.read_pid(PID_PATH)
    alive = flm_server.is_pid_alive(pid)
    host, port = flm_server.flm_host_port(FLM_BASE_URL)
    bound_pid_list = flm_server.find_pids_on_port(port)
    if pid > 0 and alive and pid not in bound_pid_list:
        flm_server.remove_pid(PID_PATH)
        pid = 0
        alive = False
    bound_pids = ",".join(str(p) for p in bound_pid_list) or "none"
    return (
        f"reachable={str(reachable).lower()} pid={pid if pid else 'none'} "
        f"pid_alive={str(alive).lower()} port_pids={bound_pids} "
        f"mode={FLM_POWER_MODE} model={FLM_MODEL}"
    )


# ── Power mode ──────────────────────────────────────────────────────────────

def get_power_mode() -> str:
    return load_config().flm_server.power_mode.value


def set_power_mode(mode: str) -> str:
    try:
        pm = config.PowerMode(mode.strip().lower())
    except ValueError:
        valid = ", ".join(m.value for m in config.PowerMode)
        raise RuntimeError(f"Invalid mode '{mode}'. Use one of: {valid}.")
    cfg = load_config()
    cfg.flm_server.power_mode = pm
    save_config(cfg)
    return pm.value


def toggle_power_mode() -> str:
    modes = list(config.PowerMode)
    current = config.PowerMode(get_power_mode())
    try:
        idx = modes.index(current)
    except ValueError:
        idx = -1
    target = modes[(idx + 1) % len(modes)]
    return set_power_mode(target.value)


# ── LLM API call ────────────────────────────────────────────────────────────

def _reset_usage_acc() -> None:
    llm_client.reset_usage_acc(_USAGE_ACC)


def _snapshot_usage_acc() -> dict:
    return llm_client.snapshot_usage_acc(_USAGE_ACC)


def call_flm_api(
    model: str,
    system_prompt: str,
    user_content: str,
    max_tokens: int,
    timeout_seconds: int,
) -> tuple[str, str]:
    """POST one chat completion; record token usage. Returns (text, model_used)."""
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content.strip()},
        ],
        "temperature": 0.1,
        "max_tokens": max(32, int(max_tokens)),
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        FLM_BASE_URL + "/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer flm"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=max(2, timeout_seconds)) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    choices = payload.get("choices") or []
    content = ""
    if choices:
        msg = choices[0].get("message") or {}
        content = str(msg.get("content") or "")
    text = llm_client.normalize_output(content)
    usage = payload.get("usage") or {}
    try:
        _USAGE_ACC["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
    except Exception as exc:
        log.debug("could not account prompt tokens: %s", exc)
    try:
        _USAGE_ACC["completion_tokens"] += int(usage.get("completion_tokens") or 0)
    except Exception as exc:
        log.debug("could not account completion tokens: %s", exc)
    return text, str(payload.get("model") or model)



# ── Model management ────────────────────────────────────────────────────────

def list_flm_models(filter_kind: str = "installed") -> dict:
    return flm_server.flm_list(filter_kind, FLM_MODEL)


# ── Doctor ──────────────────────────────────────────────────────────────────

def run_doctor() -> str:
    """Self-diagnose. Returns a multi-line key=value report."""
    checks: list[tuple[str, str]] = []
    checks.append(("python", sys.version.split()[0]))
    checks.append(("flm_base_url", FLM_BASE_URL))
    checks.append(("flm_reachable", str(is_flm_server_reachable()).lower()))
    models_info = list_flm_models()
    if "error" in models_info:
        checks.append(("model_installed", f"unknown ({models_info['error']})"))
    else:
        installed = FLM_MODEL in (models_info.get("models") or [])
        if installed:
            checks.append(("model_installed", f"yes ({FLM_MODEL})"))
        else:
            available = ",".join(models_info.get("models") or []) or "none"
            checks.append(("model_installed", f"no — wanted {FLM_MODEL}, have: {available}"))
    try:
        val = run_flm(["flm", "validate", "--json"], capture_output=True, text=True, timeout=15, check=False)
        if val.returncode == 0 and val.stdout.strip():
            try:
                vdata = json.loads(val.stdout)
                summary = ", ".join(f"{k}={v}" for k, v in vdata.items() if not isinstance(v, (dict, list)))
                checks.append(("flm_validate", summary or "ok"))
            except Exception as exc:
                log.debug("could not parse flm validate output: %s", exc)
                checks.append(("flm_validate", val.stdout.strip()[:160]))
        else:
            checks.append(("flm_validate", (val.stderr or val.stdout or "non-zero exit").strip()[:160]))
    except FileNotFoundError:
        checks.append(("flm_validate", "flm CLI not in PATH"))
    except Exception as e:
        checks.append(("flm_validate", f"error: {e}"))
    return "\n".join(f"{k}: {v}" for k, v in checks)


# ── Config patch ────────────────────────────────────────────────────────────

def apply_config_patch(patch: dict) -> str:
    """Merge a whitelisted config patch, validate model changes, refresh runtime."""
    filtered = config.filter_config_patch(patch)
    if not filtered:
        return "ok"

    old_model = FLM_MODEL
    old_log_to_file = SERVER_LOG_TO_FILE
    cfg = load_config()
    cfg_dict = asdict(cfg)
    config.deep_merge(cfg_dict, filtered)
    cfg = config.FlowkeyConfig.from_dict(cfg_dict)

    flm_patch = filtered.get("flm_server") if isinstance(filtered.get("flm_server"), dict) else None
    new_model = None
    if flm_patch and "model" in flm_patch:
        new_model = str(flm_patch["model"]).strip()
        if not new_model:
            raise RuntimeError("model cannot be empty.")
        if not _is_selectable_chat_model(new_model):
            raise RuntimeError(
                f"model {new_model!r} is not a chat-selectable model."
            )
        models_info = list_flm_models()
        if "error" not in models_info:
            installed = models_info.get("models") or []
            if new_model not in installed:
                raise RuntimeError(
                    f"Model '{new_model}' is not installed. Pull it first or pick another."
                )

    save_config(cfg)
    refresh_runtime_config()

    if new_model is not None:
        if FLM_MODEL != new_model:
            raise RuntimeError(
                f"Config model mismatch after save: wanted {new_model!r}, "
                f"got {FLM_MODEL!r}"
            )
        if FLM_MODEL != old_model:
            try:
                stop_flm_server(force=True)
            except Exception as exc:
                log.warning("stop_flm_server after model change failed: %s", exc)
            _do_server_start_and_warmup(FLM_MODEL)
            return f"model={FLM_MODEL} restarted"

        if not is_flm_server_reachable():
            log.info("model %s unchanged but server not running — starting", FLM_MODEL)
            _do_server_start_and_warmup(FLM_MODEL)
            return f"model={FLM_MODEL} started"

    if flm_patch and "log_to_file" in flm_patch:
        if SERVER_LOG_TO_FILE != old_log_to_file and is_flm_server_reachable():
            stop_flm_server(force=True)
            _do_server_start_and_warmup(FLM_MODEL)
            return f"logging={'on' if SERVER_LOG_TO_FILE else 'off'} — restarted"

    return "ok"


def _do_server_start_and_warmup(model: str) -> None:
    try:
        start_flm_server(force_restart=True)
    except Exception as exc:
        log.warning("start_flm_server for %s failed: %s", model, exc)
        return
    try:
        warmup_request(model)
    except Exception as exc:
        log.warning(
            "model %s started but warmup request failed (may still be loading): %s",
            model, exc,
        )


# ── Config snapshot ─────────────────────────────────────────────────────────

def build_config_snapshot() -> dict:
    """Build the live config snapshot consumed by the dashboard."""
    cfg = load_config()
    return {
        "version": APP_VERSION,
        "flm_api": {
            "url": cfg.flm_api.url,
            "timeout_s": cfg.flm_api.timeout_s,
        },
        "flm_server": {
            "model": cfg.flm_server.model,
            "power_mode": cfg.flm_server.power_mode,
            "auto_start": cfg.flm_server.auto_start,
            "startup_timeout_s": cfg.flm_server.startup_timeout_s,
            "log_to_file": cfg.flm_server.log_to_file,
            "log_file": cfg.flm_server.log_file,
            "flm_model_loaded": is_flm_server_reachable(),
        },
        "chat": {
            "request_timeout_s": cfg.chat.request_timeout_s,
            "temperature": cfg.chat.temperature,
            "max_tokens": cfg.chat.max_tokens,
            "context_window_turns": cfg.chat.context_window_turns,
            "system_prompt": cfg.chat.system_prompt,
        },
        "slash_commands": [
            {"name": c.name, "system_prompt": c.system_prompt,
             "description": c.description, "max_tokens": c.max_tokens}
            for c in cfg.slash_commands
        ],
    }
