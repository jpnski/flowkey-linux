"""Configuration helpers for ffchat."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from copy import copy as _copy
from copy import deepcopy as _deepcopy
from dataclasses import MISSING, asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlparse

import paths as _paths

log = logging.getLogger("ffchat.config")

import threading as _threading
_config_lock = _threading.Lock()

DEFAULT_CHAT_MODEL = "gemma4-it:e4b"


def _field_default(cls: type, name: str):
    """Read the default value from a dataclass field, handling default_factory."""
    f = cls.__dataclass_fields__[name]
    if f.default is not MISSING:
        return _copy(f.default)
    return f.default_factory()


# ── Typed config dataclasses ────────────────────────────────────────────────

class PowerMode(StrEnum):
    """Power/performance mode for the FLM server."""
    POWERSAVER = "powersaver"
    BALANCED = "balanced"
    PERFORMANCE = "performance"
    TURBO = "turbo"


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
class SlashCommand:
    """A user-configurable slash command with its system prompt."""
    name: str = ""
    system_prompt: str = ""
    description: str = ""
    max_tokens: int = 512

    @classmethod
    def from_dict(cls, d: dict) -> SlashCommand:
        return cls(
            name=str(d.get("name", cls.name)),
            system_prompt=str(d.get("system_prompt", cls.system_prompt)),
            description=str(d.get("description", cls.description)),
            max_tokens=int(d.get("max_tokens", cls.max_tokens)),
        )


@dataclass
class FlowkeyConfig:
    theme: str = "textual-dark"
    flm_api: FlmApiConfig = field(default_factory=FlmApiConfig)
    flm_server: FlmServerConfig = field(default_factory=FlmServerConfig)
    chat: ChatConfig = field(default_factory=ChatConfig)
    slash_commands: list[SlashCommand] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> FlowkeyConfig:
        return cls(
            theme=str(d.get("theme", cls.theme)),
            flm_api=FlmApiConfig.from_dict(d.get("flm_api", {})),
            flm_server=FlmServerConfig.from_dict(d.get("flm_server", {})),
            chat=ChatConfig.from_dict(d.get("chat", {})),
            slash_commands=[SlashCommand.from_dict(c) for c in d.get("slash_commands", [])],
        )

    def asdict(self) -> dict:
        return {
            "theme": self.theme,
            "flm_api": asdict(self.flm_api),
            "flm_server": asdict(self.flm_server),
            "chat": asdict(self.chat),
            "slash_commands": [asdict(c) for c in self.slash_commands],
        }


# ── Default seed config ────────────────────────────────────────────────────

def _default_config_dict() -> dict:
    """Return shipped default config values (used when no config file exists)."""
    return {
        "theme": "nord",
        "flm_api": {"url": "http://127.0.0.1:52625", "timeout_s": 60},
        "flm_server": {
            "model": "gemma4-it:e4b",
            "power_mode": "turbo",
            "auto_start": True,
            "startup_timeout_s": 25,
            "pull_timeout_seconds": 900,
            "log_to_file": False,
            "log_file": "flm_server.log",
            "extra_args": [],
        },
        "chat": {
            "request_timeout_s": 240,
            "temperature": 0.3,
            "max_tokens": 2048,
            "context_window_turns": 18,
            "system_prompt": "You are a concise, helpful local assistant. Answer in Markdown. Keep replies short unless asked to elaborate.",
        },
        "slash_commands": [
            {
                "name": "grammar",
                "system_prompt": "Fix grammar, spelling, punctuation, capitalization, and obvious wording mistakes. Preserve meaning. Return only corrected text.",
                "description": "Fix grammar and wording",
                "max_tokens": 280,
            },
            {
                "name": "summarize",
                "system_prompt": "Summarize the user text as exactly 3 bullet points. Each bullet is one sentence, factual, no preamble or sign-off. Return only the bullets.",
                "description": "Summarize as 3 bullet points",
                "max_tokens": 280,
            },
            {
                "name": "explain",
                "system_prompt": "Explain the selected code, regex, or SQL in 2-3 plain-English sentences. Call out one non-obvious edge case if any. No preamble. Return only the explanation.",
                "description": "Explain code/regex/SQL",
                "max_tokens": 280,
            },
            {
                "name": "prompt",
                "system_prompt": "Rewrite the user text as a Claude-ready prompt with <task>, <context>, <constraints>, and <output_format> sections. Return only the prompt.",
                "description": "Rewrite as a Claude-ready prompt",
                "max_tokens": 1200,
            },
        ],
    }


_seed_config: dict | None = None


def _seed_config_dict() -> dict:
    """Return the shipped seed config as a flat dict (cached)."""
    global _seed_config
    if _seed_config is None:
        seed_path = _paths.CONFIG_SEED_FILE
        try:
            _seed_config = json.loads(seed_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            _seed_config = _deepcopy(_default_config_dict())
    return _deepcopy(_seed_config)


# ── Serialization ──────────────────────────────────────────────────────────

def _from_dict(d: dict) -> FlowkeyConfig:
    """Build a typed config from a parsed JSON dict, merging user values over defaults."""
    merged = _seed_config_dict()
    deep_merge(merged, d)
    return FlowkeyConfig.from_dict(merged)


def load_config(config_path: Path) -> FlowkeyConfig:
    """Load and merge user config from disk. Returns a typed FlowkeyConfig."""
    if not config_path.exists():
        return FlowkeyConfig.from_dict(_seed_config_dict())
    try:
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("config file unreadable/invalid (%s), using defaults: %s", config_path, exc)
        return FlowkeyConfig.from_dict(_seed_config_dict())
    if not isinstance(loaded, dict):
        log.warning("config file root is not an object (%s), using defaults", config_path)
        return FlowkeyConfig.from_dict(_seed_config_dict())
    return _from_dict(loaded)


def save_config(config_path: Path, cfg: FlowkeyConfig) -> None:
    """Atomic write with a module lock to avoid torn JSON under concurrency."""
    payload = json.dumps(cfg.asdict(), ensure_ascii=False, indent=2) + "\n"
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
_PATCH_CHAT_KEYS = frozenset({
    "request_timeout_s", "temperature", "max_tokens",
    "context_window_turns", "system_prompt",
})
_PATCH_SLASH_COMMAND_KEYS = frozenset({"name", "system_prompt", "description", "max_tokens"})

_PATCH_SECTION_KEYS: dict[str, frozenset[str]] = {
    "flm_api": _PATCH_FLM_API_KEYS,
    "flm_server": _PATCH_FLM_SERVER_KEYS,
    "chat": _PATCH_CHAT_KEYS,
    "slash_commands": _PATCH_SLASH_COMMAND_KEYS,
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
        if key in _PATCH_SECTION_KEYS and isinstance(value, dict):
            allowed = _PATCH_SECTION_KEYS[key]
            filtered = {k: v for k, v in value.items() if k in allowed}
            if filtered:
                out[key] = filtered
    return out
