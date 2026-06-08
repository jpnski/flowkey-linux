"""Chat Settings panel — server config + tone preset selectors for Config tab."""

from __future__ import annotations

import asyncio
from functools import partial
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
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
    "tone-formal": "Formal",
}


class ChatSettingsPanel(Vertical):
    """Interactive settings: server config + tone preset on a single row.

    Three equally-sized columns, each with a labelled RadioSet:
      FLM Server Auto-Start  |  Performance Mode  |  Response Tone
      ○ On  ○ Off            |  ○ Balanced  ○ Max  |  ○ Friendly  ○ Casual  ○ Formal
    """

    DEFAULT_CSS = """
    ChatSettingsPanel {
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
        text-style: bold;
        padding: 0 0;
        margin-bottom: 0;
        margin-top: 1;
    }
    .settings-col > RadioSet {
        margin-bottom: 0;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._current_preset: str = "formal"
        self._auto_start: bool = True
        self._performance_mode: str = "balanced"

    def compose(self) -> ComposeResult:
        yield Static("Chat Settings", classes="panel-header")
        with Horizontal(classes="settings-row"):
            # -- Auto-Start --
            with Vertical(classes="settings-col"):
                yield Static("FLM server auto-start", classes="col-label")
                with RadioSet(id="auto-start-radio-set"):
                    yield RadioButton("On", id="auto-start-on")
                    yield RadioButton("Off", id="auto-start-off")
            # -- Performance Mode --
            with Vertical(classes="settings-col"):
                yield Static("Performance mode", classes="col-label")
                with RadioSet(id="perf-radio-set"):
                    yield RadioButton("Balanced", id="perf-balanced")
                    yield RadioButton("Max", id="perf-max")
            # -- Response Tone --
            with Vertical(classes="settings-col"):
                yield Static("Response tone", classes="col-label")
                with RadioSet(id="tone-radio-set"):
                    yield RadioButton("Friendly", id="tone-friendly")
                    yield RadioButton("Casual", id="tone-casual")
                    yield RadioButton("Formal", id="tone-formal")

    # ---- Data ingestion (called by ConfigPane) ----

    def update_tone(self, preset: str) -> None:
        """Set the tone radio set to match the current config preset."""
        self._current_preset = preset if preset in _PRESET_TO_RADIO else "formal"
        radio_id = _PRESET_TO_RADIO[self._current_preset]
        try:
            radio_set = self.query_one("#tone-radio-set", RadioSet)
            radio_set.value = radio_id
        except Exception:
            pass

    def update_server_settings(self, auto_start: bool, performance_mode: str) -> None:
        """Set both server radio sets from a config snapshot."""
        self._auto_start = auto_start
        self._performance_mode = performance_mode
        try:
            auto_radio = self.query_one("#auto-start-radio-set", RadioSet)
            auto_radio.value = "auto-start-on" if auto_start else "auto-start-off"
        except Exception:
            pass
        try:
            perf_radio = self.query_one("#perf-radio-set", RadioSet)
            perf_radio.value = "perf-balanced" if performance_mode == "balanced" else "perf-max"
        except Exception:
            pass

    # ---- Event handlers ----

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        radio_set_id = str(event.radio_set.id or "")
        radio_id = str(event.pressed.id or "") if event.pressed else ""

        if radio_set_id == "auto-start-radio-set":
            if radio_id == "auto-start-on":
                if self._auto_start:
                    return
                self.run_worker(
                    partial(self._apply_server_patch, {"auto_start": True}),
                    exclusive=True,
                )
            elif radio_id == "auto-start-off":
                if not self._auto_start:
                    return
                self.run_worker(
                    partial(self._apply_server_patch, {"auto_start": False}),
                    exclusive=True,
                )

        elif radio_set_id == "perf-radio-set":
            if radio_id == "perf-balanced":
                if self._performance_mode == "balanced":
                    return
                self.run_worker(
                    partial(self._apply_server_patch, {"performance_mode": "balanced"}),
                    exclusive=True,
                )
            elif radio_id == "perf-max":
                if self._performance_mode == "max":
                    return
                self.run_worker(
                    partial(self._apply_server_patch, {"performance_mode": "max"}),
                    exclusive=True,
                )

        elif radio_set_id == "tone-radio-set":
            preset = _RADIO_TO_PRESET.get(radio_id)
            if preset is None or preset == self._current_preset:
                return
            self.run_worker(
                partial(self._apply_tone_change, preset), exclusive=True,
            )

    # ---- Workers ----

    async def _apply_server_patch(self, server_patch: dict) -> None:
        old_auto_start = self._auto_start
        old_perf = self._performance_mode

        # Optimistic update.
        if "auto_start" in server_patch:
            self._auto_start = server_patch["auto_start"]
        if "performance_mode" in server_patch:
            self._performance_mode = server_patch["performance_mode"]
        self.update_server_settings(self._auto_start, self._performance_mode)

        resp = await asyncio.to_thread(
            _daemon_post, "apply_config_patch",
            {"patch": {"server": server_patch}},
        )
        if resp.get("ok"):
            self.app.notify("Server setting updated", severity="information")
            try:
                from tui.dashboard import DashboardWidget
                self.app.query_one(DashboardWidget).refresh_now()
            except Exception:
                pass
        else:
            # Revert on failure.
            self._auto_start = old_auto_start
            self._performance_mode = old_perf
            self.update_server_settings(self._auto_start, self._performance_mode)
            self.app.notify(
                f"Failed to update: {resp.get('error', 'unknown')}",
                severity="error", timeout=5,
            )

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
