"""Configuration helpers for Flowkey."""

from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse

import paths as _paths

log = logging.getLogger("flowkey.config")

_config_lock = threading.Lock()

CLAUDE_PROMPT_SYSTEM_PROMPT = (
    "Rewrite the user text as a Claude-ready prompt. "
    "Structure: <task> (one primary deliverable, one sentence), "
    "<context> (background facts only, no instructions), "
    "<constraints> (concrete + testable: length, format, tone), "
    "<output_format> (exact shape — Markdown headers, JSON keys, etc.). "
    "Keep each section short. No meta-framing, no preamble. "
    "Preserve intent and emoji. Return only the prompt."
)

DEFAULT_CHAT_MODEL = "gemma4-it:e4b"

DEFAULT_CONFIG = {
    "theme": "textual-dark",
    "flm_config": {
        "api_url": "http://127.0.0.1:52625",
        "api_call_timeout_s": 60,
        "power_mode": "balanced",
        "active_model": DEFAULT_CHAT_MODEL,
    },
    "flm_serving_config": {
        "auto_start": True,
        "proc_startup_timeout_s": 25,
        "log_to_file": False,
        "log_file": "flm_server.log",
        "extra_args": [],
    },
    "input_processing": {
        "enabled": True,
        "input_length_threshold": 4000,
        "chunk_size": 800,
        "min_chunk_size": 200,
    },
    "grammar_ignore_words": [],
    "hotkeys": {
        "grammar_fix": "ctrl+alt+g",
        "open_chat": "ctrl+alt+t",
        "capture_note": "ctrl+alt+n",
        "ask_chat": "ctrl+alt+a",
    },
    "history_config": {
        "store_text": False,
        "hist_file": "grammar_fix_history.jsonl",
    },
    "notes": {
        "vault_dir": "$HOME/Documents/Flowkey_Notes",
        "categories": [
            "work/technical",
            "work/managerial",
            "work/career",
            "research",
            "personal",
            "ideas",
        ],
        "fetch_timeout_seconds": 8,
        "max_extracted_chars": 2000,
        "low_confidence_to_inbox": True,
        "generate_title": True,
        "generate_summary": True,
    },
    "chat_config": {
        "request_timeout_s": 240,
        "temperature": 0.3,
        "max_tokens": 1024,
        "context_window_turns": 12,
        "system_prompt": "You are a concise, helpful local assistant. Answer in Markdown. Keep replies short unless asked to elaborate.",
    },
    "modes": {
        "grammar": {
            "label": "Grammar fix",
            "shortcut": "Ctrl+Shift+G",
            "description": "Fix grammar and wording while preserving meaning.",
            "system_prompt": "Fix grammar, spelling, punctuation, capitalization, and obvious wording mistakes. Preserve meaning. Keep emoji/smiley characters exactly as written when possible. Return only corrected text.",
            "max_tokens": {"short": 160, "medium": 220, "long": 180},
        },
        "prompt": {
            "label": "Prompt fix (Claude)",
            "description": "Rewrite rough text into a Claude-ready prompt (use prompt: prefix).",
            "system_prompt": CLAUDE_PROMPT_SYSTEM_PROMPT,
            "max_tokens": {"short": 700, "medium": 900, "long": 1200},
        },
        "summarize": {
            "label": "Summarize",
            "description": "3-bullet summary of selected text (use summarize: prefix).",
            "system_prompt": "Summarize the user text as exactly 3 bullet points. Each bullet is one sentence, factual, no preamble or sign-off. Preserve emoji/smiley characters when relevant. Return only the bullets.",
            "max_tokens": {"short": 160, "medium": 220, "long": 180},
        },
        "explain": {
            "label": "Explain code/regex/SQL",
            "description": "Plain-English explanation of selected code, regex, or query (use explain: prefix).",
            "system_prompt": "Explain the selected code, regex, or SQL in 2-3 plain-English sentences. Call out one non-obvious edge case if any. No preamble. Return only the explanation.",
            "max_tokens": {"short": 160, "medium": 220, "long": 180},
        },
        "tone": {
            "label": "Tone shift",
            "description": "Rewrite in selected tone (use tone: prefix). Active preset cycles from the tray.",
            "preset": "formal",
            "presets": {
                "formal": {"system_prompt": "Rewrite the user text in a formal, professional tone. Preserve meaning and emoji/smiley. Return only the rewritten text."},
                "casual": {"system_prompt": "Rewrite the user text in a casual, conversational tone. Preserve meaning and emoji/smiley. Return only the rewritten text."},
                "friendly": {"system_prompt": "Rewrite the user text in a warm, friendly tone. Preserve meaning and emoji/smiley. Return only the rewritten text."},
            },
            "system_prompt": "Rewrite the user text in a formal, professional tone. Preserve meaning and emoji/smiley. Return only the rewritten text.",
            "max_tokens": {"short": 160, "medium": 220, "long": 180},
        },
    },
}


def load_config(config_path: Path) -> dict:
    """Merge the user config file over DEFAULT_CONFIG."""
    if not config_path.exists():
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("config file unreadable/invalid (%s), using defaults: %s", config_path, exc)
        return copy.deepcopy(DEFAULT_CONFIG)
    if not isinstance(loaded, dict):
        log.warning("config file root is not an object (%s), using defaults", config_path)
        return copy.deepcopy(DEFAULT_CONFIG)
    merged = copy.deepcopy(DEFAULT_CONFIG)
    deep_merge(merged, loaded)
    return merged


def save_config(config_path: Path, cfg: dict) -> None:
    """Atomic write with a module lock to avoid torn JSON under concurrency."""
    payload = json.dumps(cfg, ensure_ascii=False, indent=2) + "\n"
    with _config_lock:
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = config_path.with_suffix(config_path.suffix + ".tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, config_path)
        except OSError as exc:
            log.warning("failed to save config %s: %s", config_path, exc)
            raise


def deep_merge(dst: dict, src: dict) -> None:
    """In-place merge of `src` into `dst`. Nested dicts are merged recursively."""
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            deep_merge(dst[key], value)
        else:
            dst[key] = value


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

_PATCH_FLM_CONFIG_KEYS = frozenset({"active_model", "api_url", "api_call_timeout_s", "power_mode"})
_PATCH_FLM_SERVING_KEYS = frozenset({"auto_start", "log_file", "log_to_file", "extra_args", "proc_startup_timeout_s"})
_PATCH_HISTORY_CONFIG_KEYS = frozenset({"hist_file", "store_text"})
_PATCH_INPUT_PROCESSING_KEYS = frozenset({
    "chunk_size",
    "enabled",
    "input_length_threshold",
    "min_chunk_size",
})
_PATCH_NOTES_KEYS = frozenset({
    "categories",
    "fetch_timeout_seconds",
    "generate_summary",
    "generate_title",
    "low_confidence_to_inbox",
    "max_extracted_chars",
    "vault_dir",
})
_PATCH_HOTKEYS_KEYS = frozenset({"ask_chat", "capture_note", "grammar_fix", "open_chat"})
_PATCH_TONE_KEYS = frozenset({"preset"})


def validate_patch_file(path: Path) -> Path:
    """Reject patch file paths outside temp/data/config dirs."""
    resolved = path.resolve()
    if not resolved.is_file():
        raise ValueError(f"patch file does not exist: {path}")
    temp_root = Path(tempfile.gettempdir()).resolve()
    allowed_roots = (
        temp_root,
        _paths.DATA_DIR.resolve(),
        _paths.CONFIG_FILE.parent.resolve(),
    )
    for root in allowed_roots:
        try:
            resolved.relative_to(root)
            return resolved
        except ValueError:
            continue
    raise ValueError(f"patch file outside allowed directories: {path}")


def validate_flm_base_url(url: str) -> str:
    """Reject non-loopback FLM URLs (SSRF guard for tampered config)."""
    cleaned = str(url or "").strip().rstrip("/")
    if not cleaned:
        cleaned = str(DEFAULT_CONFIG.get("flm_config", {}).get("api_url") or "http://127.0.0.1:52625")
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"flm_base_url must use http/https, got {url!r}")
    host = (parsed.hostname or "").lower()
    if host not in _LOOPBACK_HOSTS:
        raise ValueError(f"flm_base_url must be loopback, got {url!r}")
    return cleaned


def filter_config_patch(patch: dict) -> dict:
    """Whitelist keys accepted by apply_config_patch."""
    if not isinstance(patch, dict):
        raise ValueError("patch must be a JSON object")
    out: dict = {}
    for key, value in patch.items():
        if key == "flm_config" and isinstance(value, dict):
            filtered = {k: v for k, v in value.items() if k in _PATCH_FLM_CONFIG_KEYS}
            if filtered:
                out[key] = filtered
        elif key == "flm_serving_config" and isinstance(value, dict):
            filtered = {k: v for k, v in value.items() if k in _PATCH_FLM_SERVING_KEYS}
            if filtered:
                out[key] = filtered
        elif key == "history_config" and isinstance(value, dict):
            filtered = {k: v for k, v in value.items() if k in _PATCH_HISTORY_CONFIG_KEYS}
            if filtered:
                out[key] = filtered
        elif key == "input_processing" and isinstance(value, dict):
            filtered = {k: v for k, v in value.items() if k in _PATCH_INPUT_PROCESSING_KEYS}
            if filtered:
                out[key] = filtered
        elif key == "notes" and isinstance(value, dict):
            filtered = {k: v for k, v in value.items() if k in _PATCH_NOTES_KEYS}
            if filtered:
                out[key] = filtered
        elif key == "hotkeys" and isinstance(value, dict):
            filtered = {k: v for k, v in value.items() if k in _PATCH_HOTKEYS_KEYS}
            if filtered:
                out[key] = filtered
        elif key == "modes" and isinstance(value, dict):
            tone = value.get("tone")
            if isinstance(tone, dict):
                filtered_tone = {k: v for k, v in tone.items() if k in _PATCH_TONE_KEYS}
                if filtered_tone:
                    out.setdefault("modes", {})["tone"] = filtered_tone
    return out
