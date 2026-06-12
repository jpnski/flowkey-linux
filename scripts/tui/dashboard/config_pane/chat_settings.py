"""FLM Server panel — power mode + server auto-start for Config tab."""

from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import RadioButton, RadioSet, Static

from tui.dashboard import DashboardWidget
from tui.dashboard._daemon import _daemon_post

log = logging.getLogger("flowkey.tui.dashboard")


class FlmServerPanel(Vertical):
    """Server settings: power mode + auto-start, side by side."""

    DEFAULT_CSS = """
    FlmServerPanel {
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
        self._auto_start: bool = True
        self._power_mode: str = "balanced"
        self._log_to_file: bool = True

    def compose(self) -> ComposeResult:
        yield Static("FLM Server", classes="panel-header")
        with Horizontal(classes="settings-row"):
            with Vertical(classes="settings-col"):
                yield Static("Power mode", classes="col-label")
                with RadioSet(id="power-radio-set"):
                    yield RadioButton("Power Saver", id="powersaver")
                    yield RadioButton("Balanced", id="balanced")
                    yield RadioButton("Performance", id="performance")
                    yield RadioButton("Turbo", id="turbo")
            with Vertical(classes="settings-col"):
                yield Static("Server auto-start", classes="col-label")
                with RadioSet(id="auto-start-radio-set"):
                    yield RadioButton("On", id="auto-start-on")
                    yield RadioButton("Off", id="auto-start-off")
            with Vertical(classes="settings-col"):
                yield Static("Logging", classes="col-label")
                with RadioSet(id="logging-radio-set"):
                    yield RadioButton("On", id="log-to-file-on")
                    yield RadioButton("Off", id="log-to-file-off")

    # ---- Data ingestion (called by ConfigPane) ----

    def update_server_settings(self, auto_start: bool, power_mode: str, log_to_file: bool = True) -> None:
        """Set all server radio sets from a config snapshot.

        ``RadioSet`` has no ``value`` property — the correct way to select
        a button programmatically is ``RadioButton.value = True`` on the
        target button (the RadioSet handles mutual exclusion).
        """
        self._auto_start = auto_start
        self._power_mode = power_mode
        self._log_to_file = log_to_file
        try:
            target_id = "auto-start-on" if auto_start else "auto-start-off"
            self.query_one(f"#{target_id}", RadioButton).value = True
        except Exception as exc:
            log.warning("could not set auto-start radio: %s", exc)
        try:
            pm = power_mode if power_mode in {"powersaver", "balanced", "performance", "turbo"} else "balanced"
            self.query_one(f"#{pm}", RadioButton).value = True
        except Exception as exc:
            log.warning("could not set power-mode radio: %s", exc)
        try:
            target_id = "log-to-file-on" if log_to_file else "log-to-file-off"
            self.query_one(f"#{target_id}", RadioButton).value = True
        except Exception as exc:
            log.warning("could not set log-to-file radio: %s", exc)

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

        elif radio_set_id == "power-radio-set":
            if radio_id not in {"powersaver", "balanced", "performance", "turbo"}:
                return
            if radio_id == self._power_mode:
                return
            self.run_worker(
                partial(self._apply_server_patch, {"power_mode": radio_id}),
                exclusive=True,
            )

        elif radio_set_id == "logging-radio-set":
            enabled = radio_id == "log-to-file-on"
            if enabled == self._log_to_file:
                return
            self.run_worker(
                partial(self._apply_server_patch, {"log_to_file": enabled}),
                exclusive=True,
            )

    # ---- Workers ----

    async def _apply_server_patch(self, server_patch: dict) -> None:
        old_auto_start = self._auto_start
        old_perf = self._power_mode
        old_log = self._log_to_file

        if "power_mode" in server_patch:
            self._power_mode = server_patch["power_mode"]
        if "auto_start" in server_patch:
            self._auto_start = server_patch["auto_start"]
        if "log_to_file" in server_patch:
            self._log_to_file = server_patch["log_to_file"]

        self.update_server_settings(self._auto_start, self._power_mode, self._log_to_file)

        patch: dict = {}
        if "power_mode" in server_patch:
            patch.setdefault("flm_server", {})["power_mode"] = server_patch["power_mode"]
        if "auto_start" in server_patch:
            patch.setdefault("flm_server", {})["auto_start"] = server_patch["auto_start"]
        if "log_to_file" in server_patch:
            patch.setdefault("flm_server", {})["log_to_file"] = server_patch["log_to_file"]

        resp = await asyncio.to_thread(
            _daemon_post, "apply_config_patch",
            {"patch": patch},
        )
        if resp.get("ok"):
            self.app.notify("Server setting updated", severity="information")
            try:
                self.app.query_one(DashboardWidget).refresh_now()
            except Exception as exc:
                log.warning("could not refresh dashboard after server update: %s", exc)
        else:
            self._auto_start = old_auto_start
            self._power_mode = old_perf
            self._log_to_file = old_log
            self.update_server_settings(self._auto_start, self._power_mode, self._log_to_file)
            self.app.notify(
                f"Failed to update: {resp.get('error', 'unknown')}",
                severity="error", timeout=5,
            )
