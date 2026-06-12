"""Chat configuration panel — LLM interaction parameters for Config tab."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, Static

from tui.dashboard._daemon import _daemon_post

log = logging.getLogger("flowkey.tui.dashboard")

_CHAT_DEFAULTS: dict[str, float] = {
    "temperature": 0.3,
    "max_tokens": 1024.0,
    "context_window_turns": 12.0,
}
_CHAT_BOUNDS: dict[str, tuple[float, float]] = {
    "temperature": (0.0, 2.0),
    "max_tokens": (1.0, 32768.0),
    "context_window_turns": (1.0, 100.0),
}
_CHAT_STEPS: dict[str, float] = {
    "temperature": 0.1,
    "max_tokens": 128.0,
    "context_window_turns": 1.0,
}

_FIELD_INPUT_IDS: dict[str, str] = {
    "temperature": "chat-temperature",
    "max_tokens": "chat-max-tokens",
    "context_window_turns": "chat-context-turns",
}

_FIELD_IS_FLOAT: dict[str, bool] = {
    "temperature": True,
    "max_tokens": False,
    "context_window_turns": False,
}

# Maps widget id prefix (chat- prefix stripped, no -dec/-inc/-reset suffix) → field name.
_ID_TO_FIELD: dict[str, str] = {
    "temperature": "temperature",
    "max-tokens": "max_tokens",
    "context-turns": "context_window_turns",
}


class ChatPanel(Vertical):
    """Chat LLM configuration: temperature, max tokens, context window turns."""

    DEFAULT_CSS = """
    ChatPanel {
        height: auto;
        border: solid $surface;
        padding: 0 1;
        margin: 0;
    }
    .settings-row {
        height: auto;
    }
    .settings-col {
        width: 33.33%;
        height: auto;
        padding: 0 1;
    }
    .col-label {
        color: $text-muted;
        margin-top: 1;
        margin-bottom: 0;
    }
    .chat-input-row {
        height: 3;
        align: left middle;
    }
    .chat-input {
        width: 12;
    }
    .chat-step-btn {
        width: 3;
        height: 3;
        padding: 0 1;
        content-align: center middle;
        color: $text-muted;
    }
    .chat-step-btn:hover {
        color: $text;
        background: $surface;
    }
    .chat-reset-btn {
        width: 3;
        height: 3;
        padding: 0 1;
        content-align: center middle;
        color: $text-muted;
    }
    .chat-reset-btn:hover {
        color: $text;
        background: $surface;
    }
    """

    STALE_THRESHOLD: float = 12.0  # skip config snapshot updates within this many seconds of a user change (must be > REFRESH_INTERVAL=10)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._temperature: float = 0.3
        self._max_tokens: int = 1024
        self._context_turns: int = 12
        self._last_user_change: float = 0.0

    def compose(self) -> ComposeResult:
        yield Static("Chat", classes="panel-header")
        with Horizontal(classes="settings-row"):
            # -- Temperature --
            with Vertical(classes="settings-col"):
                yield Static("Temperature", classes="col-label")
                with Horizontal(classes="chat-input-row"):
                    yield Static("−", id="chat-temperature-dec", classes="chat-step-btn")
                    yield Input(value=str(self._temperature), id="chat-temperature", classes="chat-input", type="number")
                    yield Static("+", id="chat-temperature-inc", classes="chat-step-btn")
                    yield Static("↺", id="chat-temperature-reset", classes="chat-reset-btn")
            # -- Max tokens --
            with Vertical(classes="settings-col"):
                yield Static("Max tokens", classes="col-label")
                with Horizontal(classes="chat-input-row"):
                    yield Static("−", id="chat-max-tokens-dec", classes="chat-step-btn")
                    yield Input(value=str(self._max_tokens), id="chat-max-tokens", classes="chat-input", type="integer")
                    yield Static("+", id="chat-max-tokens-inc", classes="chat-step-btn")
                    yield Static("↺", id="chat-max-tokens-reset", classes="chat-reset-btn")
            # -- Context window turns --
            with Vertical(classes="settings-col"):
                yield Static("Context turns", classes="col-label")
                with Horizontal(classes="chat-input-row"):
                    yield Static("−", id="chat-context-turns-dec", classes="chat-step-btn")
                    yield Input(value=str(self._context_turns), id="chat-context-turns", classes="chat-input", type="integer")
                    yield Static("+", id="chat-context-turns-inc", classes="chat-step-btn")
                    yield Static("↺", id="chat-context-turns-reset", classes="chat-reset-btn")

    # ---- Data ingestion (called by ConfigPane) ----

    def update_config(self, chat_cfg: dict) -> None:
        """Set all three input fields from the daemon config snapshot.

        Skips the update if the user has made a change within the last
        STALE_THRESHOLD seconds, preventing the periodic dashboard refresh
        from overwriting in-flight user edits with stale snapshot data.
        """
        if time.monotonic() - self._last_user_change < self.STALE_THRESHOLD:
            return
        self._temperature = float(chat_cfg.get("temperature") or _CHAT_DEFAULTS["temperature"])
        self._max_tokens = int(chat_cfg.get("max_tokens") or _CHAT_DEFAULTS["max_tokens"])
        self._context_turns = int(chat_cfg.get("context_window_turns") or _CHAT_DEFAULTS["context_window_turns"])
        self._sync_inputs()

    def _sync_inputs(self) -> None:
        """Push instance values back into the Input widgets."""
        try:
            self.query_one("#chat-temperature", Input).value = f"{self._temperature:.1f}"
        except Exception as exc:
            log.warning("could not sync temperature input: %s", exc)
        try:
            self.query_one("#chat-max-tokens", Input).value = str(self._max_tokens)
        except Exception as exc:
            log.warning("could not sync max-tokens input: %s", exc)
        try:
            self.query_one("#chat-context-turns", Input).value = str(self._context_turns)
        except Exception as exc:
            log.warning("could not sync context-turns input: %s", exc)

    # ---- Value helpers ----

    def _read_temperature(self) -> float:
        try:
            return float(self.query_one("#chat-temperature", Input).value.strip())
        except (ValueError, Exception):
            return self._temperature

    def _read_max_tokens(self) -> int:
        try:
            return int(self.query_one("#chat-max-tokens", Input).value.strip())
        except (ValueError, Exception):
            return self._max_tokens

    def _read_context_turns(self) -> int:
        try:
            return int(self.query_one("#chat-context-turns", Input).value.strip())
        except (ValueError, Exception):
            return self._context_turns

    @staticmethod
    def _clamp(value: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, value))

    # ---- Event handlers ----

    def on_click(self, event: Static.Clicked) -> None:
        widget_id = str(event.widget.id or "")

        suffix = None
        if widget_id.endswith("-dec"):
            suffix = "-dec"
        elif widget_id.endswith("-inc"):
            suffix = "-inc"
        elif widget_id.endswith("-reset"):
            suffix = "-reset"
        if suffix is None:
            return
        base = widget_id[:-len(suffix)].replace("chat-", "", 1)
        field = _ID_TO_FIELD.get(base)
        if field is None:
            return
        if suffix == "-dec":
            self._step_field(field, -1)
        elif suffix == "-inc":
            self._step_field(field, +1)
        else:
            self._reset_field(field)

    def _step_field(self, field: str, direction: int) -> None:
        self._last_user_change = time.monotonic()
        input_id = _FIELD_INPUT_IDS.get(field)
        if not input_id:
            return
        try:
            w = self.query_one(f"#{input_id}", Input)
            raw = w.value.strip()
            current = float(raw) if _FIELD_IS_FLOAT.get(field, False) else int(raw)
        except (ValueError, Exception):
            return
        step = _CHAT_STEPS.get(field, 1.0)
        lo, hi = _CHAT_BOUNDS.get(field, (0.0, 1.0))
        new_val = self._clamp(current + direction * step, lo, hi)
        if _FIELD_IS_FLOAT.get(field, False):
            new_val = round(new_val, 1)
        else:
            new_val = int(new_val)
        w.value = str(new_val)
        if field == "temperature":
            self._temperature = new_val
        elif field == "max_tokens":
            self._max_tokens = new_val
        elif field == "context_window_turns":
            self._context_turns = new_val
        self.run_worker(self._apply_chat_patch(), exclusive=True)

    def _reset_field(self, field: str) -> None:
        self._last_user_change = time.monotonic()
        default = _CHAT_DEFAULTS.get(field)
        if default is None:
            return
        input_id = _FIELD_INPUT_IDS.get(field)
        if not input_id:
            return
        try:
            w = self.query_one(f"#{input_id}", Input)
            if _FIELD_IS_FLOAT.get(field, False):
                w.value = f"{default:.1f}"
            else:
                w.value = str(int(default))
        except Exception as exc:
            log.warning("could not reset field %s: %s", field, exc)
        if field == "temperature":
            self._temperature = default
        elif field == "max_tokens":
            self._max_tokens = int(default)
        elif field == "context_window_turns":
            self._context_turns = int(default)
        self.run_worker(self._apply_chat_patch(), exclusive=True)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._on_input_changed(event)

    def on_input_changed(self, event: Input.Changed) -> None:
        event.stop()
        self._on_input_changed(event)

    def _on_input_changed(self, _event: Input.Changed) -> None:
        """Validate current values and apply on user change/submit.

        Only sets the stale guard when a *real* change is detected (input
        differs from instance state). Programmatic calls from ``_sync_inputs``
        (triggered by ``update_config``) always set inputs to match instance
        values, so the ``if`` block is never entered and ``_last_user_change``
        stays untouched — allowing the next periodic refresh to apply the
        daemon snapshot.
        """
        temp = self._clamp(self._read_temperature(), 0.0, 2.0)
        tokens = int(self._clamp(float(self._read_max_tokens()), 1.0, 32768.0))
        turns = int(self._clamp(float(self._read_context_turns()), 1.0, 100.0))
        if temp != self._temperature or tokens != self._max_tokens or turns != self._context_turns:
            self._last_user_change = time.monotonic()
            self._temperature = temp
            self._max_tokens = tokens
            self._context_turns = turns
            self._sync_inputs()
            self.run_worker(self._apply_chat_patch(), exclusive=True)

    # ---- Workers ----

    async def _apply_chat_patch(self) -> None:
        old_temp = self._temperature
        old_tokens = self._max_tokens
        old_turns = self._context_turns

        patch = {
            "chat": {
                "temperature": self._temperature,
                "max_tokens": self._max_tokens,
                "context_window_turns": self._context_turns,
            }
        }
        resp = await asyncio.to_thread(
            _daemon_post, "apply_config_patch",
            {"patch": patch},
        )
        if not resp.get("ok"):
            self._temperature = old_temp
            self._max_tokens = old_tokens
            self._context_turns = old_turns
            self._sync_inputs()
            self.app.notify(
                f"Failed to update chat config: {resp.get('error', 'unknown')}",
                severity="error", timeout=5,
            )
        else:
            self.app.notify("Chat setting updated", severity="information")
