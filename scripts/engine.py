"""Flowkey grammar/prompt CLI (Linux).

Pipeline: read selected text → route to FastFlowLM (`/v1/chat/completions`) →
return corrected text + emit timing/token metrics on stderr → append a row to
the JSONL history. Also exposes a small `--app-action` / `--server` surface so
the tray/dashboard can manage the local FLM server and read aggregate stats.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
import urllib.request
from dataclasses import asdict
from pathlib import Path

import config
import flm_server
import llm_client
import paths as _paths
import telemetry
import updater

_is_selectable_chat_model = flm_server._is_selectable_chat_model

try:
    from importlib.metadata import version as _pkg_version
    APP_VERSION = _pkg_version("flowkey")
except Exception:
    APP_VERSION = "0.0.0"  # dev/uninstalled fallback; logger not available yet

# Run a one-time migration of pre-v1.2.0 layouts (files under scripts/) into
# the new folders. Idempotent + cheap — does nothing if everything's already
# in place.
_paths.migrate_legacy_layout()
_paths.ensure_dirs()

TOOL_DIR = _paths.SCRIPTS_DIR          # kept for callers that still resolve relative paths
CONFIG_PATH = _paths.CONFIG_FILE
PID_PATH = _paths.FLM_PID_FILE

log = logging.getLogger("flowkey.grammar")


def load_config() -> config.FlowkeyConfig:
    return config.load_config(CONFIG_PATH)


def save_config(cfg: config.FlowkeyConfig) -> None:
    config.save_config(CONFIG_PATH, cfg)
    refresh_runtime_config()


def refresh_runtime_config() -> None:
    global CONFIG, FLM_BASE_URL, FLM_MODEL, FLM_TIMEOUT_SECONDS
    global HISTORY_PATH, HISTORY_STORE_TEXT, FLM_CFG, SERVER_CFG, SERVER_AUTO_START
    global FLM_POWER_MODE, SERVING_STARTUP_TIMEOUT_S
    global SERVING_EXTRA_ARGS, SERVER_LOG_TO_FILE, SERVER_LOG_FILE
    global INPUT_PROCESSING_CFG, GRAMMAR_IGNORE_WORDS

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
    HISTORY_PATH = _paths.DATA_DIR / CONFIG.history.hist_file
    HISTORY_STORE_TEXT = CONFIG.history.store_text
    FLM_CFG = CONFIG.flm_api
    SERVER_CFG = CONFIG.flm_server
    SERVER_AUTO_START = CONFIG.flm_server.auto_start
    FLM_POWER_MODE = CONFIG.flm_server.power_mode.strip().lower()
    SERVING_STARTUP_TIMEOUT_S = CONFIG.flm_server.startup_timeout_s
    SERVING_EXTRA_ARGS = [str(a) for a in CONFIG.flm_server.extra_args]
    SERVER_LOG_TO_FILE = CONFIG.flm_server.log_to_file
    SERVER_LOG_FILE = CONFIG.flm_server.log_file
    INPUT_PROCESSING_CFG = CONFIG.input_processing
    GRAMMAR_IGNORE_WORDS = [str(w) for w in CONFIG.grammar_ignore_words if str(w).strip()]


refresh_runtime_config()

# Token usage accumulator across all sub-calls made during one call_flm() run.
_USAGE_ACC = {"prompt_tokens": 0, "completion_tokens": 0}


def _reset_usage_acc() -> None:
    llm_client.reset_usage_acc(_USAGE_ACC)


def _snapshot_usage_acc() -> dict:
    return llm_client.snapshot_usage_acc(_USAGE_ACC)


def shortcut_to_compact(shortcut: str) -> str:
    """Translate `Ctrl+Shift+G` style strings into compact modifier notation (`^+g`).

    ^ = Ctrl, + = Shift, ! = Alt, # = Win/Super.
    """
    raw = str(shortcut or "").replace(" ", "")
    if not raw:
        return ""
    parts = [part for part in raw.split("+") if part]
    if not parts:
        return ""
    key = parts[-1]
    modifiers = ""
    for part in parts[:-1]:
        lower = part.lower()
        if lower in ("ctrl", "control", "^"):
            modifiers += "^"
        elif lower in ("shift", "+"):
            modifiers += "+"
        elif lower in ("alt", "option", "!"):
            modifiers += "!"
        elif lower in ("win", "windows", "cmd", "command", "#"):
            modifiers += "#"
    return modifiers + key.lower()


def list_hotkeys() -> None:
    """Print one TSV row per configured mode (consumed by --list-hotkeys)."""
    for mode_id, mode_cfg in CONFIG.modes.items():
        hotkey = shortcut_to_compact(mode_cfg.shortcut)
        if hotkey:
            label = (mode_cfg.label or mode_id).replace("\t", " ").strip()
            print(f"{mode_id}\t{hotkey}\t{label}")


def normalize_output(text: str) -> str:
    return llm_client.normalize_output(text)


def append_history(entry: dict) -> None:
    telemetry.append_history(HISTORY_PATH, entry)


def _flm_host_port() -> tuple[str, int]:
    return flm_server.flm_host_port(FLM_BASE_URL)


def is_flm_server_reachable() -> bool:
    return flm_server.is_flm_server_reachable(FLM_BASE_URL)


def _read_pid() -> int:
    return flm_server.read_pid(PID_PATH)


def _write_pid(pid: int) -> None:
    flm_server.write_pid(PID_PATH, pid)


def _remove_pid() -> None:
    flm_server.remove_pid(PID_PATH)


def _is_pid_alive(pid: int) -> bool:
    return flm_server.is_pid_alive(pid)


def _kill_pid(pid: int) -> bool:
    return flm_server.kill_pid(pid)


def _find_pids_on_port(port: int) -> list[int]:
    return flm_server.find_pids_on_port(port)


def warmup_request(model: str | None = None) -> None:
    flm_server.warmup_request(model or FLM_MODEL, FLM_TIMEOUT_SECONDS, _call_flm_api)


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
        settings,
        _call_flm_api,
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


def get_power_mode() -> str:
    cfg = load_config()
    return cfg.flm_server.power_mode.value


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


def get_history_text_mode() -> str:
    cfg = load_config()
    return "visible" if cfg.history.store_text else "redacted"


def set_history_text_mode(mode: str) -> str:
    normalized = str(mode or "").strip().lower()
    if normalized not in {"visible", "redacted"}:
        raise RuntimeError("Invalid history mode. Use visible or redacted.")
    cfg = load_config()
    cfg.history.store_text = normalized == "visible"
    save_config(cfg)
    return normalized


def toggle_history_text_mode() -> str:
    target = "visible" if get_history_text_mode() == "redacted" else "redacted"
    return set_history_text_mode(target)


def get_tone_preset() -> str:
    cfg = load_config()
    tone = cfg.modes.get("tone")
    preset = tone.preset.strip().lower() if tone else "formal"
    return preset if preset in {"formal", "casual", "friendly"} else "formal"


def cycle_tone_preset() -> str:
    order = ["formal", "casual", "friendly"]
    current = get_tone_preset()
    nxt = order[(order.index(current) + 1) % len(order)] if current in order else order[0]
    cfg = load_config()
    if "tone" not in cfg.modes:
        cfg.modes["tone"] = config.ToneModeConfig()
    cfg.modes["tone"].preset = nxt
    save_config(cfg)
    return nxt


def server_status() -> str:
    """Return a single-line key=value summary used by the dashboard/tray."""
    reachable = is_flm_server_reachable()
    pid = _read_pid()
    alive = _is_pid_alive(pid)
    _host, port = _flm_host_port()
    bound_pid_list = _find_pids_on_port(port)
    if pid > 0 and alive and pid not in bound_pid_list:
        _remove_pid()
        pid = 0
        alive = False
    bound_pids = ",".join(str(p) for p in bound_pid_list) or "none"
    return (
        f"reachable={str(reachable).lower()} pid={pid if pid else 'none'} "
        f"pid_alive={str(alive).lower()} port_pids={bound_pids} "
        f"mode={FLM_POWER_MODE} model={FLM_MODEL}"
    )


def parse_mode() -> str:
    args = list(sys.argv[1:])
    if "--mode" not in args:
        return "grammar"
    idx = args.index("--mode")
    if idx + 1 >= len(args):
        raise RuntimeError("Missing value for --mode.")
    mode = str(args[idx + 1]).strip().lower()
    modes = CONFIG.modes
    if mode not in modes:
        known = ", ".join(sorted(modes.keys())) if modes else "none"
        raise RuntimeError(f"Unknown mode '{mode}'. Known modes: {known}.")
    return mode


def _split_chunks(text: str, chunk_size: int) -> list[str]:
    return llm_client.split_chunks(text, chunk_size, INPUT_PROCESSING_CFG)


def _resolve_token_budget(mode: str, input_text: str) -> tuple[int, str]:
    runtime = llm_client.LlmRuntimeConfig(
        base_url=FLM_BASE_URL,
        model=FLM_MODEL,
        timeout_seconds=FLM_TIMEOUT_SECONDS,
        server_auto_start=SERVER_AUTO_START,
        input_processing_cfg=INPUT_PROCESSING_CFG,
        protected_words=GRAMMAR_IGNORE_WORDS,
        modes_cfg=CONFIG.modes,
    )
    return llm_client.resolve_token_budget(runtime, mode, input_text)


def _call_flm_api(
    model: str,
    system_prompt: str,
    user_content: str,
    max_tokens: int,
    timeout_seconds: int,
) -> tuple[str, str]:
    """POST one chat completion; record token usage into _USAGE_ACC. Returns (text, model_used)."""
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content.strip()},
            ],
            "temperature": 0.1,
            "max_tokens": max(32, int(max_tokens)),
            "stream": False,
        }
    ).encode("utf-8")
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
    text = normalize_output(content)
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


def _line_reuse_ratio(input_text: str, output_text: str) -> float:
    return llm_client.line_reuse_ratio(input_text, output_text)


def _word_set(text: str) -> set[str]:
    return llm_client.word_set(text)


def _word_overlap_ratio(a: str, b: str) -> float:
    return llm_client.word_overlap_ratio(a, b)


def _looks_like_prompt_text(text: str) -> bool:
    return llm_client.looks_like_prompt_text(text)


def _force_prompt_shape(input_text: str) -> str:
    return llm_client.force_prompt_shape(input_text)


def _strip_prompt_scaffold_labels(text: str) -> str:
    return llm_client.strip_prompt_scaffold_labels(text)


def _dict_protect(text: str) -> tuple[str, dict[str, str]]:
    return llm_client.dict_protect(text, GRAMMAR_IGNORE_WORDS)


def _dict_restore(text: str, mapping: dict[str, str]) -> str:
    return llm_client.dict_restore(text, mapping)


def call_flm(mode: str, input_text: str) -> tuple[str, float, str, str]:
    runtime = llm_client.LlmRuntimeConfig(
        base_url=FLM_BASE_URL,
        model=FLM_MODEL,
        timeout_seconds=FLM_TIMEOUT_SECONDS,
        server_auto_start=SERVER_AUTO_START,
        input_processing_cfg=INPUT_PROCESSING_CFG,
        protected_words=GRAMMAR_IGNORE_WORDS,
        modes_cfg=CONFIG.modes,
    )
    return llm_client.call_flm(
        runtime,
        mode,
        input_text,
        _call_flm_api,
        is_flm_server_reachable,
        start_flm_server,
        _USAGE_ACC,
    )


def _read_input_text() -> str:
    args = list(sys.argv[1:])
    if "--input-file" in args:
        idx = args.index("--input-file")
        if idx + 1 >= len(args):
            raise RuntimeError("Missing value for --input-file.")
        path = Path(args[idx + 1])
        return path.read_text(encoding="utf-8")
    return sys.stdin.read()


def _write_output_text(text: str) -> None:
    args = list(sys.argv[1:])
    if "--output-file" in args:
        idx = args.index("--output-file")
        if idx + 1 >= len(args):
            raise RuntimeError("Missing value for --output-file.")
        path = Path(args[idx + 1])
        path.write_text(text, encoding="utf-8")
        return
    print(text)


def compute_usage_stats() -> dict:
    return telemetry.compute_usage_stats(HISTORY_PATH)


def compute_dashboard_data() -> dict:
    return telemetry.compute_dashboard_data(HISTORY_PATH)


def _flm_list(filter_kind: str) -> dict:
    return flm_server.flm_list(filter_kind, FLM_MODEL)


def list_flm_models(filter_kind: str = "installed") -> dict:
    return _flm_list(filter_kind)


def call_flm_simple(
    system_prompt: str,
    user_content: str,
    *,
    max_tokens: int = 400,
    timeout_seconds: int | None = None,
) -> tuple[str, str]:
    """Low-level single chat completion using current model/config. Returns (text, model_used)."""
    return _call_flm_api(
        FLM_MODEL, system_prompt, user_content,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds or FLM_TIMEOUT_SECONDS,
    )


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
        val = subprocess.run(["flm", "validate", "--json"], capture_output=True, text=True, timeout=15, check=False)
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
    for name in ("grammar_fix_history.jsonl", "prompt_history.jsonl", "chat_threads.jsonl"):
        p = _paths.DATA_DIR / name
        try:
            with p.open("a", encoding="utf-8"):
                pass
            checks.append((f"writable:{name}", "yes"))
        except Exception as e:
            checks.append((f"writable:{name}", f"no ({e})"))
    return "\n".join(f"{k}: {v}" for k, v in checks)


UPDATE_FEED_URL_DEFAULT = updater.UPDATE_FEED_URL_DEFAULT


def _version_tuple(v: str) -> tuple[int, ...]:
    return updater.version_tuple(v)


def check_for_update() -> dict:
    feed_url = CONFIG.update.feed_url or UPDATE_FEED_URL_DEFAULT
    return updater.check_for_update(APP_VERSION, feed_url=feed_url)


def apply_update() -> str:
    feed_url = CONFIG.update.feed_url or UPDATE_FEED_URL_DEFAULT
    return updater.apply_update(APP_VERSION, TOOL_DIR, feed_url=feed_url)


def apply_config_patch(patch: dict) -> str:
    """Merge a whitelisted config patch, validate model changes, refresh runtime."""
    filtered = config.filter_config_patch(patch)
    if not filtered:
        return "ok"

    old_model = FLM_MODEL
    old_log_to_file = SERVER_LOG_TO_FILE
    cfg = load_config()
    # deep_merge operates on dicts; convert dataclass → dict → merge → reconstruct
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
                f"model {new_model!r} is not a chat-selectable model. "
                f"Embedding/ASR models are reserved for side-loading; pick a chat model instead."
            )
        models_info = list_flm_models()
        if "error" not in models_info:
            installed = models_info.get("models") or []
            if new_model not in installed:
                raise RuntimeError(
                    f"Model '{new_model}' is not installed. Pull it first or pick another."
                )
        # Legacy keys cleaned up by config dataclass; no manual pop needed.

    save_config(cfg)
    refresh_runtime_config()

    if new_model is not None:
        if FLM_MODEL != new_model:
            raise RuntimeError(
                f"Config model mismatch after save: wanted {new_model!r}, "
                f"got {FLM_MODEL!r}"
            )
        if FLM_MODEL != old_model:
            # Model changed: stop old server, start new one.
            try:
                stop_flm_server(force=True)
            except Exception as exc:
                log.warning("stop_flm_server after model change failed: %s", exc)
            _do_server_start_and_warmup(FLM_MODEL)
            return f"model={FLM_MODEL} restarted"

        # Model name is the same, but the server may not be running yet
        # (first-time load in the daemon's current lifecycle).
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
    """Start the FLM server for *model* and wait until it responds to
    a real API call (warmup), confirming the model is loaded in memory."""
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


def build_config_snapshot() -> dict:
    """Build the live config snapshot consumed by the dashboard.

    Single source of truth — used by both the daemon's `config_snapshot`
    action (fast path, in-process) and the CLI `--app-action config_snapshot`
    branch (fallback when the daemon is down). Keeps the two paths bit-for-bit
    identical so the dashboard renders the same values either way.
    """
    cfg = load_config()
    tone = cfg.modes.get("tone") if "tone" in cfg.modes else None
    tone_preset = tone.preset if tone else "formal"
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
        "history": {
            "store_text": cfg.history.store_text,
        },
        "input_processing": {
            "enabled": cfg.input_processing.enabled,
            "input_length_threshold": cfg.input_processing.input_length_threshold,
            "chunk_size": cfg.input_processing.chunk_size,
            "min_chunk_size": cfg.input_processing.min_chunk_size,
        },
        "notes": {
            "vault_dir": cfg.notes.vault_dir,
            "categories": cfg.notes.categories,
            "fetch_timeout_seconds": cfg.notes.fetch_timeout_seconds,
            "max_extracted_chars": cfg.notes.max_extracted_chars,
            "low_confidence_to_inbox": cfg.notes.low_confidence_to_inbox,
            "generate_title": cfg.notes.generate_title,
            "generate_summary": cfg.notes.generate_summary,
        },
        "tone": {
            "preset": tone_preset,
        },
        "hotkeys": {
            "grammar_fix": cfg.hotkeys.grammar_fix,
            "open_chat": cfg.hotkeys.open_chat,
            "capture_note": cfg.hotkeys.capture_note,
            "ask_chat": cfg.hotkeys.ask_chat,
        },
    }


def handle_server_cli() -> bool:
    """Dispatch --app-action / --server subcommands. Returns True if a CLI branch handled the call."""
    args = list(sys.argv[1:])
    if "--app-action" in args:
        idx = args.index("--app-action")
        if idx + 1 >= len(args):
            raise RuntimeError("Missing value for --app-action.")
        action = str(args[idx + 1]).strip().lower()
        if action == "status":
            print(server_status())
            return True
        if action == "start":
            print(start_flm_server(force_restart=False))
            return True
        if action == "warmup":
            start_flm_server(force_restart=False)
            warmup_request()
            print("warmed_up")
            return True
        if action == "restart":
            print(start_flm_server(force_restart=True))
            return True
        if action == "stop":
            print("stopped" if stop_flm_server(force=True) else "not_running")
            return True
        if action == "performance":
            print(get_power_mode())
            return True
        if action == "power_mode":
            print(get_power_mode())
            return True
        if action == "toggle_power_mode":
            print(toggle_power_mode())
            return True
        if action.startswith("set_power_"):
            pm = action.removeprefix("set_power_")
            print(set_power_mode(pm))
            return True
        if action == "history_text_status":
            print(get_history_text_mode())
            return True
        if action == "toggle_history_text":
            print(toggle_history_text_mode())
            return True
        if action == "stats":
            print(json.dumps(compute_usage_stats(), ensure_ascii=False))
            return True
        if action == "dashboard_data":
            print(json.dumps(compute_dashboard_data(), ensure_ascii=False))
            return True
        if action == "config_snapshot":
            # Subprocess fallback for the dashboard when the daemon is down.
            # Same dict the daemon's _act_config_snapshot returns.
            print(json.dumps(build_config_snapshot(), ensure_ascii=False))
            return True
        if action == "models_list":
            print(json.dumps(list_flm_models(), ensure_ascii=False))
            return True
        if action == "tone_preset":
            print(get_tone_preset())
            return True
        if action == "cycle_tone_preset":
            print(cycle_tone_preset())
            return True
        if action == "doctor":
            print(run_doctor())
            return True
        if action == "set_history_visible":
            print(set_history_text_mode("visible"))
            return True
        if action == "set_history_redacted":
            print(set_history_text_mode("redacted"))
            return True
        if action in ("set_tone_formal", "set_tone_casual", "set_tone_friendly"):
            target = action.removeprefix("set_tone_")
            cfg = load_config()
            if "tone" not in cfg.modes:
                cfg.modes["tone"] = config.ToneModeConfig()
            cfg.modes["tone"].preset = target
            save_config(cfg)
            print(target)
            return True
        if action == "apply_config_patch":
            # Reads a JSON patch from --file and merges into the current config.
            if "--file" not in args:
                raise RuntimeError("apply_config_patch requires --file.")
            fidx = args.index("--file")
            if fidx + 1 >= len(args):
                raise RuntimeError("Missing value for --file.")
            patch_path = config.validate_patch_file(Path(args[fidx + 1]))
            patch = json.loads(patch_path.read_text(encoding="utf-8"))
            print(apply_config_patch(patch))
            return True
        if action == "models_installed":
            print(json.dumps(_flm_list("installed"), ensure_ascii=False))
            return True
        if action == "models_not_installed":
            print(json.dumps(_flm_list("not-installed"), ensure_ascii=False))
            return True
        if action == "remove_model":
            if "--value" not in args:
                raise RuntimeError("remove_model requires --value <model_name>.")
            vidx = args.index("--value")
            if vidx + 1 >= len(args):
                raise RuntimeError("Missing value for --value.")
            model_name = str(args[vidx + 1]).strip()
            if not model_name:
                raise RuntimeError("Model name is empty.")
            try:
                result = subprocess.run(["flm", "remove", model_name],
                                        capture_output=True, text=True, timeout=60, check=False)
            except FileNotFoundError:
                raise RuntimeError("flm CLI not found in PATH.")
            output = (result.stdout or "") + (result.stderr or "")
            if result.returncode != 0:
                raise RuntimeError(f"flm remove failed (exit {result.returncode}):\n{output.strip()}")
            print(output.strip() or f"removed {model_name}")
            return True
        if action == "version":
            print(APP_VERSION)
            return True
        if action == "update_check":
            print(json.dumps(check_for_update(), ensure_ascii=False))
            return True
        if action == "update_apply":
            print(apply_update())
            return True
        if action == "pull_model":
            if "--value" not in args:
                raise RuntimeError("pull_model requires --value <model_name>.")
            vidx = args.index("--value")
            if vidx + 1 >= len(args):
                raise RuntimeError("Missing value for --value.")
            model_name = str(args[vidx + 1]).strip()
            if not model_name:
                raise RuntimeError("Model name is empty.")
            try:
                _cfg = load_config()
                _pull_timeout = _cfg.flm_server.pull_timeout_seconds
                result = subprocess.run(
                    ["flm", "pull", model_name],
                    capture_output=True,
                    text=True,
                    timeout=_pull_timeout,
                    check=False,
                )
            except FileNotFoundError:
                raise RuntimeError("flm CLI not found in PATH.")
            output = (result.stdout or "") + (result.stderr or "")
            if result.returncode != 0:
                raise RuntimeError(f"flm pull failed (exit {result.returncode}):\n{output.strip()}")
            print(output.strip() or f"pulled {model_name}")
            return True
        raise RuntimeError("Unknown --app-action.")

    if "--server" not in args:
        return False
    idx = args.index("--server")
    if idx + 1 >= len(args):
        raise RuntimeError("Missing value for --server. Use start|stop|status|restart.")
    cmd = str(args[idx + 1]).strip().lower()

    if cmd == "start":
        print(start_flm_server(force_restart=False))
        return True
    if cmd == "warmup":
        start_flm_server(force_restart=False)
        warmup_request()
        print("warmed_up")
        return True
    if cmd == "restart":
        print(start_flm_server(force_restart=True))
        return True
    if cmd == "stop":
        print("stopped" if stop_flm_server(force=True) else "not_running")
        return True
    if cmd == "status":
        print(server_status())
        return True

    raise RuntimeError("Unknown --server command. Use start|warmup|stop|status|restart.")


def main() -> None:
    if "--list-hotkeys" in sys.argv:
        list_hotkeys()
        return
    if handle_server_cli():
        return
    mode = parse_mode()
    input_text = _read_input_text().strip()
    if not input_text:
        return

    corrected, elapsed, model_used, strategy = call_flm(mode, input_text)
    usage = _snapshot_usage_acc()
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    tok_per_sec = round(completion_tokens / elapsed, 2) if (elapsed and elapsed > 0 and completion_tokens > 0) else 0.0
    history_entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "status": "success",
        "mode": mode,
        "source": "fastflowlm",
        "model": model_used,
        "input_chars": len(input_text),
        "output_chars": len(corrected),
        "strategy": strategy,
        "elapsed_seconds": elapsed,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "tok_per_sec": tok_per_sec,
    }
    if HISTORY_STORE_TEXT:
        history_entry["input_text"] = input_text
        history_entry["output_text"] = corrected
    append_history(history_entry)
    _write_output_text(corrected)
    print(f"API_TIME={elapsed}", file=sys.stderr)
    print(f"API_PROMPT_TOKENS={prompt_tokens}", file=sys.stderr)
    print(f"API_COMPLETION_TOKENS={completion_tokens}", file=sys.stderr)
    print(f"API_TOK_PER_SEC={tok_per_sec}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except subprocess.TimeoutExpired:
        print(f"FastFlowLM timed out after {FLM_TIMEOUT_SECONDS}s.", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
