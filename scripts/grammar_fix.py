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
from pathlib import Path

import actions
import flm_server
import llm_client
import paths as _paths
import telemetry
import updater

import config

_is_selectable_chat_model = flm_server._is_selectable_chat_model

try:
    from importlib.metadata import version as _pkg_version
    APP_VERSION = _pkg_version("flowkey")
except Exception:
    APP_VERSION = "0.0.0"

# Run a one-time migration of pre-v1.2.0 layouts (files under scripts/) into
# the new folders. Idempotent + cheap — does nothing if everything's already
# in place.
_paths.migrate_legacy_layout()
_paths.ensure_dirs()

TOOL_DIR = _paths.SCRIPTS_DIR          # kept for callers that still resolve relative paths
CONFIG_PATH = _paths.CONFIG_FILE
PID_PATH = _paths.FLM_PID_FILE

DEFAULT_CONFIG = config.DEFAULT_CONFIG
log = logging.getLogger("flowkey.grammar")


def load_config() -> dict:
    return config.load_config(CONFIG_PATH)


def save_config(cfg: dict) -> None:
    config.save_config(CONFIG_PATH, cfg)
    refresh_runtime_config()


def refresh_runtime_config() -> None:
    global CONFIG, FLM_BASE_URL, FLM_MODEL, FLM_TIMEOUT_SECONDS
    global HISTORY_PATH, HISTORY_STORE_TEXT, FLM_CFG, SERVING_CFG, SERVER_AUTO_START
    global FLM_POWER_MODE, SERVING_STARTUP_TIMEOUT_S
    global SERVING_EXTRA_ARGS, SERVER_LOG_TO_FILE, SERVER_LOG_FILE
    global INPUT_PROCESSING_CFG, GRAMMAR_IGNORE_WORDS

    CONFIG = load_config()
    try:
        FLM_BASE_URL = config.validate_flm_base_url(
            str(CONFIG.get("flm_config", {}).get("api_url") or "http://127.0.0.1:52625")
        )
    except ValueError as exc:
        log.warning("invalid flm_base_url in config, using default: %s", exc)
        FLM_BASE_URL = "http://127.0.0.1:52625"
    FLM_MODEL = str(CONFIG.get("flm_config", {}).get("active_model") or "gemma4-it:e4b").strip()
    if FLM_MODEL and not _is_selectable_chat_model(FLM_MODEL):
        log.warning(
            "active_model %r in config is not a chat-selectable model; "
            "falling back to default %r",
            FLM_MODEL, config.DEFAULT_CHAT_MODEL,
        )
        FLM_MODEL = config.DEFAULT_CHAT_MODEL
    FLM_TIMEOUT_SECONDS = int(CONFIG.get("flm_config", {}).get("api_call_timeout_s") or 30)
    HISTORY_PATH = _paths.DATA_DIR / str(CONFIG.get("history_config", {}).get("hist_file") or "grammar_fix_history.jsonl")
    HISTORY_STORE_TEXT = bool(CONFIG.get("history_config", {}).get("store_text", False))
    FLM_CFG = CONFIG.get("flm_config") or {}
    SERVING_CFG = CONFIG.get("flm_serving_config") or {}
    SERVER_AUTO_START = bool(SERVING_CFG.get("auto_start", True))
    FLM_POWER_MODE = str(FLM_CFG.get("power_mode") or "balanced").strip().lower()
    SERVING_STARTUP_TIMEOUT_S = int(SERVING_CFG.get("proc_startup_timeout_s") or 25)
    SERVING_EXTRA_ARGS = [str(a) for a in (SERVING_CFG.get("extra_args") or [])]
    SERVER_LOG_TO_FILE = bool(SERVING_CFG.get("log_to_file", True))
    SERVER_LOG_FILE = str(SERVING_CFG.get("log_file") or "flm_server.log")
    INPUT_PROCESSING_CFG = CONFIG.get("input_processing") or {}
    GRAMMAR_IGNORE_WORDS = [str(w) for w in (CONFIG.get("grammar_ignore_words") or []) if str(w).strip()]


refresh_runtime_config()

PERF_TO_PMODE = flm_server.PERF_TO_PMODE

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
    for mode_id, mode_cfg in (CONFIG.get("modes") or {}).items():
        hotkey = shortcut_to_compact((mode_cfg or {}).get("shortcut"))
        if hotkey:
            label = str((mode_cfg or {}).get("label") or mode_id).replace("\t", " ").strip()
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


def _warmup_request(model: str) -> None:
    flm_server.warmup_request(model, FLM_TIMEOUT_SECONDS, _call_flm_api)


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
    mode = str((cfg.get("flm_config") or {}).get("power_mode") or "balanced").strip().lower()
    return mode if mode in {"balanced", "max"} else "balanced"


def set_power_mode(mode: str) -> str:
    normalized = str(mode or "").strip().lower()
    if normalized not in {"powersaver", "balanced", "performance", "turbo"}:
        raise RuntimeError(f"Invalid mode '{normalized}'. Use powersaver, balanced, performance, or turbo.")
    cfg = load_config()
    cfg.setdefault("flm_config", {})["power_mode"] = normalized
    save_config(cfg)
    return normalized


_PERF_CYCLE = ["powersaver", "balanced", "performance", "turbo"]


def toggle_power_mode() -> str:
    current = get_power_mode()
    try:
        idx = _PERF_CYCLE.index(current)
    except ValueError:
        idx = -1
    target = _PERF_CYCLE[(idx + 1) % len(_PERF_CYCLE)]
    return set_power_mode(target)


def get_history_text_mode() -> str:
    cfg = load_config()
    enabled = bool(cfg.get("history_config", {}).get("store_text", False))
    return "visible" if enabled else "redacted"


def set_history_text_mode(mode: str) -> str:
    normalized = str(mode or "").strip().lower()
    if normalized not in {"visible", "redacted"}:
        raise RuntimeError("Invalid history mode. Use visible or redacted.")
    cfg = load_config()
    cfg.setdefault("history_config", {})["store_text"] = normalized == "visible"
    save_config(cfg)
    return normalized


def toggle_history_text_mode() -> str:
    target = "visible" if get_history_text_mode() == "redacted" else "redacted"
    return set_history_text_mode(target)


def get_tone_preset() -> str:
    cfg = load_config()
    tone = ((cfg.get("modes") or {}).get("tone") or {})
    preset = str(tone.get("preset") or "formal").strip().lower()
    return preset if preset in {"formal", "casual", "friendly"} else "formal"


def cycle_tone_preset() -> str:
    order = ["formal", "casual", "friendly"]
    current = get_tone_preset()
    nxt = order[(order.index(current) + 1) % len(order)] if current in order else order[0]
    cfg = load_config()
    cfg.setdefault("modes", {}).setdefault("tone", {})["preset"] = nxt
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
    modes = CONFIG.get("modes") or {}
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
        modes_cfg=CONFIG.get("modes") or {},
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
    except Exception:
        pass
    try:
        _USAGE_ACC["completion_tokens"] += int(usage.get("completion_tokens") or 0)
    except Exception:
        pass
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
        modes_cfg=CONFIG.get("modes") or {},
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


def list_flm_models() -> dict:
    """Installed models on this FLM server (alias for filter=installed)."""
    return _flm_list("installed")


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
            except Exception:
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
    feed_url = str(((CONFIG.get("update") or {}).get("feed_url")) or UPDATE_FEED_URL_DEFAULT)
    return updater.check_for_update(APP_VERSION, feed_url=feed_url)


def apply_update() -> str:
    feed_url = str(((CONFIG.get("update") or {}).get("feed_url")) or UPDATE_FEED_URL_DEFAULT)
    return updater.apply_update(APP_VERSION, TOOL_DIR, feed_url=feed_url)


def _deep_merge(dst: dict, src: dict) -> None:
    config.deep_merge(dst, src)


def apply_config_patch(patch: dict) -> str:
    """Merge a whitelisted config patch, validate model changes, refresh runtime."""
    filtered = config.filter_config_patch(patch)
    if not filtered:
        return "ok"

    old_model = FLM_MODEL
    cfg = load_config()
    _deep_merge(cfg, filtered)

    flm_patch = filtered.get("flm_config") if isinstance(filtered.get("flm_config"), dict) else None
    new_model = None
    if flm_patch and "active_model" in flm_patch:
        new_model = str(flm_patch["active_model"]).strip()
        if not new_model:
            raise RuntimeError("active_model cannot be empty.")
        if not _is_selectable_chat_model(new_model):
            raise RuntimeError(
                f"active_model {new_model!r} is not a chat-selectable model. "
                f"Embedding/ASR models are reserved for side-loading; pick a chat model instead."
            )
        models_info = list_flm_models()
        if "error" not in models_info:
            installed = models_info.get("models") or []
            if new_model not in installed:
                raise RuntimeError(
                    f"Model '{new_model}' is not installed. Pull it first or pick another."
                )
        chat = cfg.get("chat")
        if isinstance(chat, dict):
            chat.pop("llm_model", None)
            chat.pop("llm_base_url", None)

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
        _warmup_request(model)
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
    notes_cfg = cfg.get("notes") or {}
    input_processing_cfg = cfg.get("input_processing") or {}
    hotkeys_cfg = cfg.get("hotkeys") or {}
    tone_cfg = ((cfg.get("modes") or {}).get("tone") or {})
    flm_cfg = cfg.get("flm_config") or {}
    serving_cfg = cfg.get("flm_serving_config") or {}
    history_cfg = cfg.get("history_config") or {}
    return {
        "version": APP_VERSION,
        "flm_config": {
            "api_url": str(flm_cfg.get("api_url") or "http://127.0.0.1:52625"),
            "active_model": str(flm_cfg.get("active_model") or FLM_MODEL),
            "api_call_timeout_s": int(flm_cfg.get("api_call_timeout_s") or 30),
            "power_mode": str(flm_cfg.get("power_mode") or "balanced"),
            "flm_model_loaded": is_flm_server_reachable(),
        },
        "flm_serving_config": {
            "auto_start": bool(serving_cfg.get("auto_start", True)),
            "proc_startup_timeout_s": int(serving_cfg.get("proc_startup_timeout_s") or 25),
            "log_to_file": bool(serving_cfg.get("log_to_file", True)),
            "log_file": str(serving_cfg.get("log_file") or "flm_server.log"),
        },
        "history_config": {
            "store_text": bool(history_cfg.get("store_text", False)),
        },
        "input_processing": {
            "enabled": bool(input_processing_cfg.get("enabled", True)),
            "input_length_threshold": int(input_processing_cfg.get("input_length_threshold") or 4000),
            "chunk_size": int(input_processing_cfg.get("chunk_size") or 800),
            "min_chunk_size": int(input_processing_cfg.get("min_chunk_size") or 200),
        },
        "notes": {
            "vault_dir": str(notes_cfg.get("vault_dir") or "$HOME/Documents/Flowkey_Notes"),
            "categories": list(notes_cfg.get("categories") or []),
            "fetch_timeout_seconds": int(notes_cfg.get("fetch_timeout_seconds") or 8),
            "max_extracted_chars": int(notes_cfg.get("max_extracted_chars") or 2000),
            "low_confidence_to_inbox": bool(notes_cfg.get("low_confidence_to_inbox", True)),
            "generate_title": bool(notes_cfg.get("generate_title", True)),
            "generate_summary": bool(notes_cfg.get("generate_summary", True)),
        },
        "tone": {
            "preset": str(tone_cfg.get("preset") or "formal"),
        },
        "hotkeys": {
            "grammar_fix": str(hotkeys_cfg.get("grammar_fix") or "ctrl+alt+g"),
            "open_chat": str(hotkeys_cfg.get("open_chat") or "ctrl+alt+t"),
            "capture_note": str(hotkeys_cfg.get("capture_note") or "ctrl+alt+n"),
            "ask_chat": str(hotkeys_cfg.get("ask_chat") or "ctrl+alt+a"),
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
            _warmup_request(FLM_MODEL)
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
        if action == "set_power_balanced":
            print(set_power_mode("balanced"))
            return True
        if action == "set_power_turbo":
            print(set_power_mode("turbo"))
            return True
        if action == "set_power_performance":
            print(set_power_mode("performance"))
            return True
        if action == "set_power_powersaver":
            print(set_power_mode("powersaver"))
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
            cfg.setdefault("modes", {}).setdefault("tone", {})["preset"] = target
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
                result = subprocess.run(
                    ["flm", "pull", model_name],
                    capture_output=True,
                    text=True,
                    timeout=actions.PULL_MODEL_TIMEOUT_SECONDS,
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
        _warmup_request(FLM_MODEL)
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
