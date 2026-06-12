"""Configuration helpers for Flowkey."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from copy import copy as _copy
from dataclasses import dataclass, field, asdict, MISSING
from enum import Enum
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


def _field_default(cls: type, name: str):
    """Read the default value from a dataclass field, handling default_factory."""
    f = cls.__dataclass_fields__[name]
    if f.default is not MISSING:
        return copy(f.default)
    return f.default_factory()


# ── Typed config dataclasses ────────────────────────────────────────────────

class PowerMode(str, Enum):
    """Power/performance mode for the FLM server."""
    POWERSAVER = "powersaver"
    BALANCED = "balanced"
    PERFORMANCE = "performance"
    TURBO = "turbo"


@dataclass
class MaxTokens:
    short: int = 160
    medium: int = 220
    long: int = 280

    @classmethod
    def from_dict(cls, d: dict) -> MaxTokens:
        return cls(
            short=int(d.get("short", cls.short)),
            medium=int(d.get("medium", cls.medium)),
            long=int(d.get("long", cls.long)),
        )


@dataclass
class FlmApiConfig:
    url: str = "http://127.0.0.1:52625"
    timeout_s: int = 60

    @classmethod
    def from_dict(cls, d: dict) -> FlmApiConfig:
        return cls(
            url=str(d.get("url", cls.url)),
            timeout_s=int(d.get("timeout_s", cls.timeout_s)),
        )


@dataclass
class FlmServerConfig:
    model: str = DEFAULT_CHAT_MODEL
    power_mode: PowerMode = PowerMode.BALANCED
    auto_start: bool = True
    startup_timeout_s: int = 25
    pull_timeout_seconds: int = 900
    log_to_file: bool = False
    log_file: str = "flm_server.log"
    extra_args: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> FlmServerConfig:
        return cls(
            model=str(d.get("model", cls.model)),
            power_mode=PowerMode(d.get("power_mode", cls.power_mode.value)),
            auto_start=bool(d.get("auto_start", cls.auto_start)),
            startup_timeout_s=int(d.get("startup_timeout_s", cls.startup_timeout_s)),
            pull_timeout_seconds=int(d.get("pull_timeout_seconds", cls.pull_timeout_seconds)),
            log_to_file=bool(d.get("log_to_file", cls.log_to_file)),
            log_file=str(d.get("log_file", cls.log_file)),
            extra_args=list(d.get("extra_args", _field_default(cls, "extra_args"))),
        )


@dataclass
class InputProcessingConfig:
    enabled: bool = True
    input_length_threshold: int = 4000
    chunk_size: int = 800
    min_chunk_size: int = 200

    @classmethod
    def from_dict(cls, d: dict) -> InputProcessingConfig:
        return cls(
            enabled=bool(d.get("enabled", cls.enabled)),
            input_length_threshold=int(d.get("input_length_threshold", cls.input_length_threshold)),
            chunk_size=int(d.get("chunk_size", cls.chunk_size)),
            min_chunk_size=int(d.get("min_chunk_size", cls.min_chunk_size)),
        )


@dataclass
class HotkeysConfig:
    grammar_fix: str = "ctrl+alt+g"
    open_chat: str = "ctrl+alt+t"
    capture_note: str = "ctrl+alt+n"
    ask_chat: str = "ctrl+alt+a"

    @classmethod
    def from_dict(cls, d: dict) -> HotkeysConfig:
        return cls(
            grammar_fix=str(d.get("grammar_fix", cls.grammar_fix)),
            open_chat=str(d.get("open_chat", cls.open_chat)),
            capture_note=str(d.get("capture_note", cls.capture_note)),
            ask_chat=str(d.get("ask_chat", cls.ask_chat)),
        )


@dataclass
class HistoryConfig:
    store_text: bool = False
    hist_file: str = "engine_history.jsonl"

    @classmethod
    def from_dict(cls, d: dict) -> HistoryConfig:
        return cls(
            store_text=bool(d.get("store_text", cls.store_text)),
            hist_file=str(d.get("hist_file", cls.hist_file)),
        )


@dataclass
class NotesConfig:
    vault_dir: str = "$HOME/Documents/Flowkey_Notes"
    categories: list[str] = field(default_factory=lambda: [
        "work/technical", "work/managerial", "work/career",
        "research", "personal", "ideas",
    ])
    fetch_timeout_seconds: int = 8
    max_extracted_chars: int = 2000
    low_confidence_to_inbox: bool = True
    generate_title: bool = True
    generate_summary: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> NotesConfig:
        return cls(
            vault_dir=str(d.get("vault_dir", cls.vault_dir)),
            categories=list(d.get("categories", _field_default(cls, "categories"))),
            fetch_timeout_seconds=int(d.get("fetch_timeout_seconds", cls.fetch_timeout_seconds)),
            max_extracted_chars=int(d.get("max_extracted_chars", cls.max_extracted_chars)),
            low_confidence_to_inbox=bool(d.get("low_confidence_to_inbox", cls.low_confidence_to_inbox)),
            generate_title=bool(d.get("generate_title", cls.generate_title)),
            generate_summary=bool(d.get("generate_summary", cls.generate_summary)),
        )


@dataclass
class ChatConfig:
    request_timeout_s: int = 240
    temperature: float = 0.3
    max_tokens: int = 1024
    context_window_turns: int = 12
    system_prompt: str = (
        "You are a concise, helpful local assistant. "
        "Answer in Markdown. Keep replies short unless asked to elaborate."
    )

    @classmethod
    def from_dict(cls, d: dict) -> ChatConfig:
        return cls(
            request_timeout_s=int(d.get("request_timeout_s", cls.request_timeout_s)),
            temperature=float(d.get("temperature", cls.temperature)),
            max_tokens=int(d.get("max_tokens", cls.max_tokens)),
            context_window_turns=int(d.get("context_window_turns", cls.context_window_turns)),
            system_prompt=str(d.get("system_prompt", cls.system_prompt)),
        )


@dataclass
class ModeTonePresetsConfig:
    formal: dict = field(default_factory=lambda: {
        "system_prompt": "Rewrite the user text in a formal, professional tone. "
                         "Preserve meaning and emoji/smiley. Return only the rewritten text.",
    })
    casual: dict = field(default_factory=lambda: {
        "system_prompt": "Rewrite the user text in a casual, conversational tone. "
                         "Preserve meaning and emoji/smiley. Return only the rewritten text.",
    })
    friendly: dict = field(default_factory=lambda: {
        "system_prompt": "Rewrite the user text in a warm, friendly tone. "
                         "Preserve meaning and emoji/smiley. Return only the rewritten text.",
    })

    @classmethod
    def from_dict(cls, d: dict) -> ModeTonePresetsConfig:
        return cls(
            formal=dict(d.get("formal", _field_default(cls, "formal"))),
            casual=dict(d.get("casual", _field_default(cls, "casual"))),
            friendly=dict(d.get("friendly", _field_default(cls, "friendly"))),
        )


@dataclass
class ToneModeConfig:
    label: str = "Tone shift"
    shortcut: str = ""
    description: str = (
        "Rewrite in selected tone (use tone: prefix). "
        "Active preset cycles from the tray."
    )
    system_prompt: str = (
        "Rewrite the user text in a formal, professional tone. "
        "Preserve meaning and emoji/smiley. Return only the rewritten text."
    )
    max_tokens: MaxTokens = field(default_factory=lambda: MaxTokens(160, 220, 280))
    preset: str = "formal"
    presets: ModeTonePresetsConfig = field(default_factory=ModeTonePresetsConfig)

    @classmethod
    def from_dict(cls, d: dict) -> ToneModeConfig:
        return cls(
            label=str(d.get("label", cls.label)),
            shortcut=str(d.get("shortcut", cls.shortcut)),
            description=str(d.get("description", cls.description)),
            system_prompt=str(d.get("system_prompt", cls.system_prompt)),
            max_tokens=MaxTokens.from_dict(d.get("max_tokens", {})),
            preset=str(d.get("preset", cls.preset)),
            presets=ModeTonePresetsConfig.from_dict(d.get("presets", {})),
        )


@dataclass
class StandardModeConfig:
    label: str = ""
    shortcut: str = ""
    description: str = ""
    system_prompt: str = ""
    max_tokens: MaxTokens = field(default_factory=MaxTokens)

    @classmethod
    def from_dict(cls, d: dict) -> StandardModeConfig:
        return cls(
            label=str(d.get("label", cls.label)),
            shortcut=str(d.get("shortcut", cls.shortcut)),
            description=str(d.get("description", cls.description)),
            system_prompt=str(d.get("system_prompt", cls.system_prompt)),
            max_tokens=MaxTokens.from_dict(d.get("max_tokens", {})),
        )


@dataclass
class UpdateConfig:
    feed_url: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> UpdateConfig:
        return cls(
            feed_url=str(d.get("feed_url", cls.feed_url)),
        )


@dataclass
class FlowkeyConfig:
    theme: str = "textual-dark"
    flm_api: FlmApiConfig = field(default_factory=FlmApiConfig)
    flm_server: FlmServerConfig = field(default_factory=FlmServerConfig)
    input_processing: InputProcessingConfig = field(default_factory=InputProcessingConfig)
    grammar_ignore_words: list[str] = field(default_factory=list)
    hotkeys: HotkeysConfig = field(default_factory=HotkeysConfig)
    history: HistoryConfig = field(default_factory=HistoryConfig)
    notes: NotesConfig = field(default_factory=NotesConfig)
    chat: ChatConfig = field(default_factory=ChatConfig)
    modes: dict[str, StandardModeConfig | ToneModeConfig] = field(default_factory=dict)
    update: UpdateConfig = field(default_factory=UpdateConfig)

    @classmethod
    def from_dict(cls, d: dict) -> FlowkeyConfig:
        raw_modes = d.get("modes", {})
        modes: dict[str, StandardModeConfig | ToneModeConfig] = {}
        for k, v in raw_modes.items():
            if k == "tone":
                modes[k] = ToneModeConfig.from_dict(v)
            else:
                modes[k] = StandardModeConfig.from_dict(v)

        return cls(
            theme=str(d.get("theme", cls.theme)),
            flm_api=FlmApiConfig.from_dict(d.get("flm_api", {})),
            flm_server=FlmServerConfig.from_dict(d.get("flm_server", {})),
            input_processing=InputProcessingConfig.from_dict(d.get("input_processing", {})),
            grammar_ignore_words=list(d.get("grammar_ignore_words", [])),
            hotkeys=HotkeysConfig.from_dict(d.get("hotkeys", {})),
            history=HistoryConfig.from_dict(d.get("history", {})),
            notes=NotesConfig.from_dict(d.get("notes", {})),
            chat=ChatConfig.from_dict(d.get("chat", {})),
            modes=modes,
            update=UpdateConfig.from_dict(d.get("update", {})),
        )


# ── Serialisation ──────────────────────────────────────────────────────────

def _from_dict(d: dict) -> FlowkeyConfig:
    """Build a typed config from a parsed JSON object, merging user values over defaults."""
    merged = asdict(FlowkeyConfig())  # defaults as flat dict for deep_merge
    deep_merge(merged, d)
    return FlowkeyConfig.from_dict(merged)


def load_config(config_path: Path) -> FlowkeyConfig:
    """Load and merge user config from disk. Returns a typed FlowkeyConfig."""
    if not config_path.exists():
        return FlowkeyConfig()
    try:
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("config file unreadable/invalid (%s), using defaults: %s", config_path, exc)
        return FlowkeyConfig()
    if not isinstance(loaded, dict):
        log.warning("config file root is not an object (%s), using defaults", config_path)
        return FlowkeyConfig()
    return _from_dict(loaded)


def save_config(config_path: Path, cfg: FlowkeyConfig) -> None:
    """Atomic write with a module lock to avoid torn JSON under concurrency."""
    payload = json.dumps(asdict(cfg), ensure_ascii=False, indent=2) + "\n"
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


# ── Validation / filtering ─────────────────────────────────────────────────

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

_PATCH_FLM_API_KEYS = frozenset({"url", "timeout_s"})
_PATCH_FLM_SERVER_KEYS = frozenset({
    "model", "power_mode", "auto_start", "startup_timeout_s",
    "log_file", "log_to_file", "extra_args",
})
_PATCH_HISTORY_KEYS = frozenset({"hist_file", "store_text"})
_PATCH_INPUT_PROCESSING_KEYS = frozenset({
    "chunk_size", "enabled", "input_length_threshold", "min_chunk_size",
})
_PATCH_NOTES_KEYS = frozenset({
    "categories", "fetch_timeout_seconds", "generate_summary",
    "generate_title", "low_confidence_to_inbox", "max_extracted_chars", "vault_dir",
})
_PATCH_HOTKEYS_KEYS = frozenset({"ask_chat", "capture_note", "grammar_fix", "open_chat"})
_PATCH_CHAT_KEYS = frozenset({
    "request_timeout_s", "temperature", "max_tokens",
    "context_window_turns", "system_prompt",
})
_PATCH_TONE_KEYS = frozenset({"preset"})

# Section name → allowed-keys mapping for filter_config_patch.
_PATCH_SECTION_KEYS: dict[str, frozenset[str]] = {
    "flm_api": _PATCH_FLM_API_KEYS,
    "flm_server": _PATCH_FLM_SERVER_KEYS,
    "history": _PATCH_HISTORY_KEYS,
    "input_processing": _PATCH_INPUT_PROCESSING_KEYS,
    "notes": _PATCH_NOTES_KEYS,
    "chat": _PATCH_CHAT_KEYS,
    "hotkeys": _PATCH_HOTKEYS_KEYS,
}


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
        cleaned = FlmApiConfig().url
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
        if key == "modes" and isinstance(value, dict):
            tone = value.get("tone")
            if isinstance(tone, dict):
                filtered_tone = {k: v for k, v in tone.items() if k in _PATCH_TONE_KEYS}
                if filtered_tone:
                    out.setdefault("modes", {})["tone"] = filtered_tone
        elif key in _PATCH_SECTION_KEYS and isinstance(value, dict):
            allowed = _PATCH_SECTION_KEYS[key]
            filtered = {k: v for k, v in value.items() if k in allowed}
            if filtered:
                out[key] = filtered
    return out
