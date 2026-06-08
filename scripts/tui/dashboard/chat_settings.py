"""Chat Settings panel — tone preset selector for Config tab."""

from __future__ import annotations

import asyncio
from functools import partial
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import RadioButton, RadioSet, Static

from tui.dashboard._daemon import _daemon_post

# Mapping between RadioButton IDs and config preset values.
_RADIO_TO_PRESET: dict[str, str] = {
    "tone-friendly": "friendly",
    "tone-casual": "casual",
    "tone-formal": "formal",
}
_PRESET_TO_RADIO: dict[str, str] = {v: k for k, v in _RADIO_TO_PRESET.items()}

# Display labels (keys match radio IDs).
_LABELS: dict[str, str] = {
    "tone-friendly": "Friendly",
    "tone-casual": "Casual",
    "tone-formal": "Professional",
}


class ChatSettingsPanel(Vertical):
    """Interactive tone preset selector for the Config tab.

    Displays a RadioSet of three mutually exclusive tone options.
    Selecting one persists the change via the daemon and refreshes the
    dashboard so all views reflect the new preset.
    """

    DEFAULT_CSS = """
    ChatSettingsPanel {
        height: auto;
        border: solid $surface;
        padding: 0 1;
        margin: 0;
    }
    #tone-radio-set {
        margin-bottom: 1;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._current_preset: str = "formal"

    def compose(self) -> ComposeResult:
        yield Static("Chat Settings", classes="panel-header")
        yield Static("Response tone", classes="subsection-header")
        with RadioSet(id="tone-radio-set"):
            yield RadioButton("Friendly", id="tone-friendly")
            yield RadioButton("Casual", id="tone-casual")
            yield RadioButton("Professional", id="tone-formal")

    # ---- Data ingestion (called by ConfigPane) ----

    def update_tone(self, preset: str) -> None:
        """Set the radio set to match the current config preset."""
        self._current_preset = preset if preset in _PRESET_TO_RADIO else "formal"
        radio_id = _PRESET_TO_RADIO[self._current_preset]
        try:
            radio_set = self.query_one("#tone-radio-set", RadioSet)
            radio_set.value = radio_id
        except Exception:
            pass

    # ---- Event handlers ----

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        if event.radio_set.id != "tone-radio-set":
            return
        radio_id = str(event.pressed.id or "") if event.pressed else ""
        preset = _RADIO_TO_PRESET.get(radio_id)
        if preset is None or preset == self._current_preset:
            return
        self.run_worker(
            partial(self._apply_tone_change, preset), exclusive=True,
        )

    # ---- Workers ----

    async def _apply_tone_change(self, preset: str) -> None:
        old_preset = self._current_preset
        self._current_preset = preset
        resp = await asyncio.to_thread(
            _daemon_post, "apply_config_patch",
            {"patch": {"modes": {"tone": {"preset": preset}}}},
        )
        if resp.get("ok"):
            display = _LABELS.get(_PRESET_TO_RADIO.get(preset, ""), preset)
            self.app.notify(f"Response tone: {display}", severity="information")
            # Refresh so the text config display and any other views update.
            try:
                from tui.dashboard import DashboardWidget
                self.app.query_one(DashboardWidget).refresh_now()
            except Exception:
                pass
        else:
            self._current_preset = old_preset
            self.app.notify(
                f"Failed to set tone: {resp.get('error', 'unknown')}",
                severity="error", timeout=5,
            )
            # Revert the radio selection.
            try:
                radio_set = self.query_one("#tone-radio-set", RadioSet)
                radio_set.value = _PRESET_TO_RADIO[old_preset]
            except Exception:
                pass
