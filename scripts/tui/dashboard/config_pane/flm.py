"""FLM Runtime and Model panel — version info, active model, model download (top of Config tab)."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from functools import partial
from typing import Any

import paths
import pull
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Click
from textual.reactive import reactive
from textual.widgets import Select, Static

import engine
import flm_server

log = logging.getLogger("ffchat.tui.dashboard")

_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_DEFAULT_TIMEOUT = 15.0

# Category → best model tag for the Download-a-model dropdown.
# When a model appears in _STARRED_TAGS, a ⭐ is shown next to it
# so first-time users know which to try.  Maintainers: update the
# value when a better model ships for a category.
_STARRED_MODELS: dict[str, str] = {
    "Multimodal":        "gemma4-it:e4b",
    "General Chat":      "gemma3:4b",
    "Reasoning":         "deepseek-r1-0528:8b",
    "Vision":            "qwen3.5:9b",
    "Tool Calling":      "qwen3-it:4b",
    "Transcript Notes":  "lfm2-trans:2.6b",
    "Translation":       "translategemma:4b",
    "Medical":           "medgemma1.5:4b",
    "Expert (MoE)":      "gpt-oss:20b",
    "Audio (ASR)":       "whisper-v3:turbo",
}
_STARRED_TAGS: frozenset[str] = frozenset(_STARRED_MODELS.values())

_FLM_UPDATE_CACHE = paths.DATA_DIR / "flm_update_cache.json"


class FlmModelPanel(Vertical):
    """FLM Runtime and Model panel for the Config tab.

    Three inline columns: current FLM runtime version | active-model Select |
    download-model Select.  Also hosts the pull-progress row and restart
    spinner that were always part of this panel.
    """

    DEFAULT_CSS = """
    FlmModelPanel {
        height: auto;
        border: solid $surface;
        padding: 0 1;
        margin: 0;
    }
    #flm-model-grid {
        layout: grid;
        grid-size: 3;
        grid-columns: 30% 34% 34%;
        grid-gutter: 1;
        height: auto;
    }
    #flm-runtime-col, #flm-active-col, #flm-download-col {
        height: auto;
    }
    #flm-version-row {
        layout: horizontal;
        height: auto;
    }
    #flm-version-box {
        border: tall $border-blurred;
        background: $surface;
        height: 3;
        padding: 0 1;
        width: 85%;
    }
    #flm-check-update-btn {
        width: 3;
        height: 3;
        padding: 0 1;
        content-align: center middle;
        color: $text-muted;
    }
    #flm-check-update-btn:hover {
        color: $text;
        background: $surface;
    }
    #flm-active-model-select { margin-bottom: 0; }
    #flm-download-select { margin-bottom: 0; }
    #flm-restart-status-line { display: none; margin-top: 1; }
    #flm-restart-status-line.active { display: block; }
    #flm-update-status {
        height: auto;
        display: none;
        margin-top: 1;
    }
    #flm-update-status.-visible {
        display: block;
    }
    /* Pull progress row — hidden by default, shown via .active */
    #flm-pull-row { display: none; height: auto; margin-top: 1; }
    #flm-pull-row.active { display: block; }
    #flm-pull-spinner { width: 1; }
    .pull-text { width: 1fr; }
    .cancel-pull-btn {
        width: 3;
        height: 1;
        padding: 0;
        text-align: center;
        color: $error;
    }
    .cancel-pull-btn:hover {
        color: $text;
        background: $surface;
    }
    """

    # Reactive state for the restarting spinner.
    restarting: reactive[bool] = reactive(False)
    restart_label: reactive[str] = reactive("")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._installed_models: list[str] = []
        self._not_installed_models: list[str] = []
        self._active_model: str = ""
        self._prior_active: str = ""
        self._spinner_index: int = 0
        self._pull_spinner_index: int = 0
        self._pull_in_flight: bool = False
        self._pull_completed_key: str = ""
        self._pull_last_notify_at: float = 0.0
        self._daemon_reachable: bool = True
        self._last_model_change_at: float = 0.0
        self._model_loaded: bool = False
        self._last_select_refresh_at: float = 0.0
        self._last_select_options: list[str] = []
        self._last_dl_select_refresh_at: float = 0.0
        self._last_dl_select_options: list[str] = []
        self._current_version: str = ""
        self._latest_version: str = ""
        self._has_update: bool = False
        self._status_timer: Any = None

    def compose(self) -> ComposeResult:
        yield Static("FLM Runtime and Model", classes="panel-header")
        with Vertical(id="flm-model-grid"):
            # --- Column 1: FLM runtime version ---
            with Vertical(id="flm-runtime-col"):
                yield Static("Current version", classes="subsection-header")
                with Horizontal(id="flm-version-row"):
                    yield Static("[dim](checking…)[/]", id="flm-version-box")
                    yield Static("🗘 ", id="flm-check-update-btn")
            # --- Column 2: Active model ---
            with Vertical(id="flm-active-col"):
                yield Static("Active model", classes="subsection-header")
                yield Select(
                    options=[("(no model loaded)", "")],
                    value="",
                    allow_blank=False,
                    prompt="Choose installed model…",
                    id="flm-active-model-select",
                    disabled=True,
                )
            # --- Column 3: Download a model ---
            with Vertical(id="flm-download-col"):
                yield Static("Download a model", classes="subsection-header")
                yield Select(
                    options=[("(select a model)", "")],
                    value="",
                    allow_blank=False,
                    prompt="(select a model)",
                    id="flm-download-select",
                    disabled=True,
                )
        # Full-width status lines below the three-column grid.
        yield Static("", id="flm-restart-status-line")
        yield Static("", id="flm-update-status")
        with Horizontal(id="flm-pull-row"):
            yield Static("", id="flm-pull-spinner")
            yield Static("", id="flm-pull-text", classes="pull-text")
            yield Static("[red]❌[/]", id="flm-cancel-pull-btn", classes="cancel-pull-btn")

    def on_mount(self) -> None:
        self.set_interval(1.0, self._refresh_pull_status)
        self.set_interval(0.15, self._tick_restart_spinner)
        self.set_interval(0.15, self._tick_pull_spinner)

    # ---- Data ingestion (called by DashboardWidget) ----

    def update_models(self, installed: list[str], not_installed: list[str],
                      active: str, model_loaded: bool = False,
                      *, daemon_reachable: bool = True) -> None:
        self._daemon_reachable = daemon_reachable
        self._installed_models = list(installed)
        self._not_installed_models = list(not_installed)
        self._model_loaded = model_loaded
        self._active_model = active if model_loaded else ""
        self._refresh_select()
        self._refresh_download_select()

    def mark_daemon_down(self) -> None:
        self._daemon_reachable = False
        self._active_model = ""
        self._installed_models = []
        self._not_installed_models = []
        try:
            row = self.query_one("#flm-pull-row")
            row.remove_class("active")
        except Exception as exc:
            log.warning("could not clear pull-row active class: %s", exc)
        self._set_select_enabled(False)
        try:
            self.query_one("#flm-download-select", Select).disabled = True
        except Exception as exc:
            log.warning("could not disable download select: %s", exc)

    # ---- Internal renderers ----

    def _refresh_select(self) -> None:
        select = self.query_one("#flm-active-model-select", Select)
        if not self._installed_models:
            self._last_select_refresh_at = time.monotonic()
            select.set_options([("(no models installed)", "")])
            self._set_select_enabled(False)
            self._last_select_options = []
            return

        options = [("(none)", "")] + [
            (name, name) for name in self._installed_models
        ]

        if self._installed_models != self._last_select_options:
            self._last_select_refresh_at = time.monotonic()
            select.set_options(options)
            self._last_select_options = list(self._installed_models)

        if not self._model_loaded:
            select.value = ""
        elif self._active_model and self._active_model in self._installed_models:
            select.value = self._active_model
        else:
            select.value = self._installed_models[0]
        self._set_select_enabled(not self.restarting)

    def _refresh_download_select(self) -> None:
        select = self.query_one("#flm-download-select", Select)
        if not self._not_installed_models:
            if self._last_dl_select_options:
                self._last_dl_select_refresh_at = time.monotonic()
                select.set_options([("(all models installed)", "")])
                self._last_dl_select_options = []
            select.disabled = True
        else:
            if self._not_installed_models != self._last_dl_select_options:
                options = [("(select a model)", "")] + [
                    (
                        f" {name}" if name in _STARRED_TAGS else name,
                        name,
                    )
                    for name in self._not_installed_models
                ]
                self._last_dl_select_refresh_at = time.monotonic()
                select.set_options(options)
                self._last_dl_select_options = list(self._not_installed_models)
            select.disabled = False

    def _set_select_enabled(self, enabled: bool) -> None:
        try:
            select = self.query_one("#flm-active-model-select", Select)
            select.disabled = not enabled
        except Exception as exc:
            log.warning("could not toggle select disabled state: %s", exc)

    # ---- Pollers ----

    @staticmethod
    def _format_pull_status(model: str, percent: float, message: str) -> str:
        size_match = re.search(r'\([^)]+/\s*[^)]+\)', message)
        size_info = f" {size_match.group(0)}" if size_match else ""
        return f" Pulling [bold]{model}[/] | {percent:.1f}%{size_info}"

    def _refresh_pull_status(self) -> None:
        try:
            result = pull.status()
        except Exception as exc:
            log.warning("pull.status() failed: %s", exc)
            return
        state = str(result.get("state") or "idle")
        model = str(result.get("model") or "")
        percent = float(result.get("percent") or 0.0)
        message = str(result.get("message") or "")
        error = str(result.get("error") or "")

        pull_row = self.query_one("#flm-pull-row")
        spinner = self.query_one("#flm-pull-spinner", Static)
        text = self.query_one("#flm-pull-text", Static)

        if state == "running":
            self._pull_completed_key = ""
            pull_row.add_class("active")
            spinner.update(f"[yellow]{_SPINNER[self._pull_spinner_index % len(_SPINNER)]}[/]")
            text.update(f"[yellow]{self._format_pull_status(model, percent, message)}[/]")
            self._pull_in_flight = True
        elif state == "done":
            self._pull_in_flight = False
            pull_row.remove_class("active")
            spinner.update("")
            text.update("")
            now = time.monotonic()
            if self._pull_completed_key != f"done:{model}" and now - self._pull_last_notify_at > 6.0:
                self._pull_completed_key = f"done:{model}"
                self._pull_last_notify_at = now
                self.app.notify(f"Pulled new model: {model}", severity="information", timeout=4)
                self.call_later(self._refresh_dashboard)
        elif state == "cancelled":
            self._pull_in_flight = False
            pull_row.remove_class("active")
            spinner.update("")
            text.update("")
            now = time.monotonic()
            if self._pull_completed_key != "cancelled" and now - self._pull_last_notify_at > 6.0:
                self._pull_completed_key = "cancelled"
                self._pull_last_notify_at = now
                self.app.notify("Pull cancelled", severity="information", timeout=4)
        elif state == "error":
            self._pull_in_flight = False
            pull_row.remove_class("active")
            spinner.update("")
            text.update("")
            error_key = f"error:{error or 'unknown'}"
            now = time.monotonic()
            if self._pull_completed_key != error_key and now - self._pull_last_notify_at > 6.0:
                self._pull_completed_key = error_key
                self._pull_last_notify_at = now
                self.app.notify(f"Pull failed: {error or 'unknown error'}", severity="error", timeout=6)
        else:  # idle
            if self._pull_in_flight:
                self._pull_in_flight = False
                pull_row.remove_class("active")
                spinner.update("")
                text.update("")

    def _tick_restart_spinner(self) -> None:
        if not self.restarting:
            return
        self._spinner_index = (self._spinner_index + 1) % len(_SPINNER)
        glyph = _SPINNER[self._spinner_index]
        try:
            line = self.query_one("#flm-restart-status-line", Static)
            line.update(f"[yellow]{glyph} {self.restart_label}[/]")
        except Exception as exc:
            log.warning("could not update restart spinner: %s", exc)

    def _tick_pull_spinner(self) -> None:
        if not self._pull_in_flight:
            return
        self._pull_spinner_index = (self._pull_spinner_index + 1) % len(_SPINNER)
        glyph = _SPINNER[self._pull_spinner_index]
        try:
            spinner = self.query_one("#flm-pull-spinner", Static)
            spinner.update(f"[yellow]{glyph}[/]")
        except Exception as exc:
            log.warning("could not update pull spinner: %s", exc)

    def _refresh_dashboard(self) -> None:
        try:
            from tui.dashboard import DashboardWidget
            self.app.query_one(DashboardWidget).refresh_now()
        except Exception as exc:
            log.warning("could not refresh dashboard: %s", exc)

    # ---- Event handlers ----

    def on_select_changed(self, event: Select.Changed) -> None:
        # ---- Active-model Select ----
        if event.select.id == "flm-active-model-select":
            new_value = str(event.value or "")

            if time.monotonic() - self._last_select_refresh_at < 1.0:
                return

            if not self._installed_models:
                return

            select = event.select

            is_streaming = False
            try:
                from tui.chat import ChatWidget
                chat = self.app.query_one(ChatWidget)
                if chat.is_streaming():
                    is_streaming = True
            except Exception as exc:
                log.warning("could not check chat streaming state: %s", exc)

            # --- "(none)" selected → unload the model ---
            if not new_value:
                if self._model_loaded:
                    if is_streaming:
                        self.app.notify(
                            "Chat is streaming — finish or cancel before unloading the model",
                            severity="warning", timeout=6,
                        )
                        select.value = self._active_model
                        return
                    self.run_worker(self._unload_model, exclusive=True)
                return

            # --- A real model was selected ---
            if new_value == self._active_model:
                return
            now = time.monotonic()
            if now - self._last_model_change_at < 10.0:
                return
            if is_streaming:
                self.app.notify(
                    "Chat is streaming — finish or cancel before changing the model",
                    severity="warning", timeout=6,
                )
                if self._active_model:
                    select.value = self._active_model
                else:
                    select.value = self._installed_models[0]
                return

            self._prior_active = self._active_model
            self._last_model_change_at = time.monotonic()
            self.run_worker(partial(self._apply_model_change, new_value), exclusive=True)
            return

        # ---- Download-model Select ----
        if event.select.id == "flm-download-select":
            if time.monotonic() - self._last_dl_select_refresh_at < 1.0:
                return

            model = str(event.value or "")
            if not model:
                return
            if self._pull_in_flight:
                self.app.notify("A pull is already in progress", severity="warning", timeout=4)
                event.select.value = ""
                return
            self.run_worker(partial(self._start_pull, model), exclusive=True)
            event.select.value = ""

    def on_click(self, event: Click) -> None:
        if event.widget.id == "flm-cancel-pull-btn":
            self.run_worker(self._cancel_pull, exclusive=True)
        elif event.widget.id == "flm-check-update-btn":
            self.run_worker(self._do_update_check, exclusive=True)

    # ---- Workers ----

    async def _apply_model_change(self, new_value: str) -> None:
        self._set_select_enabled(False)
        self.restarting = True
        self.restart_label = f"Restarting FLM, swapping to {new_value}"
        line = self.query_one("#flm-restart-status-line", Static)
        line.add_class("active")
        line.update(f"[yellow]{_SPINNER[0]} {self.restart_label}[/]")

        try:
            result = await asyncio.to_thread(
                engine.apply_config_patch, {"flm_server": {"model": new_value}},
            )
            self.app.notify(f"Active model: {new_value}", severity="information")
            try:
                from tui.chat import ChatWidget
                chat = self.app.query_one(ChatWidget)
                chat.set_model(new_value)
            except Exception as exc:
                log.warning("could not set chat model: %s", exc)
            try:
                from tui.dashboard import DashboardWidget
                self.app.query_one(DashboardWidget).refresh_now()
            except Exception as exc:
                log.warning("could not refresh dashboard after model change: %s", exc)
        except Exception as exc:
            self.app.notify(
                f"Model change failed: {exc}", severity="error", timeout=8
            )
            try:
                select = self.query_one("#flm-active-model-select", Select)
                self._last_select_refresh_at = time.monotonic()
                if self._prior_active:
                    select.value = self._prior_active
                elif self._installed_models:
                    select.value = self._installed_models[0]
            except Exception as exc:
                log.warning("could not revert model select after error: %s", exc)
        finally:
            self.restarting = False
            self.restart_label = ""
            line = self.query_one("#flm-restart-status-line", Static)
            line.remove_class("active")
            line.update("")
            self._set_select_enabled(True)

    async def _unload_model(self) -> None:
        try:
            stopped = await asyncio.to_thread(engine.stop_flm_server, True)
            if stopped:
                self.app.notify("Model unloading from memory...", severity="information", timeout=4)
            else:
                self.app.notify("No model was loaded", severity="information", timeout=4)
        except Exception as exc:
            self.app.notify(f"Failed to unload: {exc}", severity="error", timeout=6)
            return

        self._model_loaded = False
        self._active_model = ""
        try:
            from tui.chat import ChatWidget
            chat = self.app.query_one(ChatWidget)
            chat.set_model("")
        except Exception as exc:
            log.warning("could not clear chat model: %s", exc)
        self._last_select_refresh_at = time.monotonic()
        self._refresh_select()

    async def _start_pull(self, model: str) -> None:
        self._set_select_enabled(False)
        try:
            result = await asyncio.to_thread(pull.start_pull, model)
            if isinstance(result, dict) and not result.get("ok", True):
                raise RuntimeError(str(result.get("error") or "unknown error"))
            self.app.notify(f"Started pulling {model}", severity="information")
        except Exception as exc:
            self.app.notify(f"Pull start failed: {exc}", severity="error", timeout=8)
        finally:
            self._set_select_enabled(True)

    async def _cancel_pull(self) -> None:
        try:
            result = await asyncio.to_thread(pull.cancel_pull)
            if isinstance(result, dict) and not result.get("ok", True):
                raise RuntimeError(str(result.get("error") or "unknown error"))
            try:
                text = self.query_one("#flm-pull-text", Static)
                text.update("Cancelling…")
            except Exception as exc:
                log.warning("could not update pull text: %s", exc)
        except Exception as exc:
            self.app.notify(f"Cancel failed: {exc}", severity="error", timeout=6)

    # ---- Version info ----

    def update_version_info(self, data: dict) -> None:
        self._current_version = str(data.get("current") or "").strip()
        self._latest_version = str(data.get("latest") or "").strip()
        self._has_update = bool(data.get("has_update", False))
        try:
            box = self.query_one("#flm-version-box", Static)
            if self._current_version:
                box.update(self._current_version)
            else:
                box.update("[yellow]not detected[/]")
        except Exception as exc:
            log.warning("could not update version box: %s", exc)

    # ---- Status auto-hide ----

    def _schedule_status_hide(self) -> None:
        if self._status_timer is not None:
            self._status_timer.cancel()
        self._status_timer = self.set_timer(5.0, self._hide_update_status)

    def _hide_update_status(self) -> None:
        self._status_timer = None
        try:
            self.query_one("#flm-update-status", Static).remove_class("-visible")
        except Exception as exc:
            log.warning("could not hide update status: %s", exc)

    # ---- Workers ----

    async def _do_update_check(self) -> None:
        icon: Static | None = None
        try:
            icon = self.query_one("#flm-check-update-btn", Static)
            icon.update("…")
        except Exception as exc:
            log.warning("could not update check-update icon: %s", exc)

        try:
            data = await asyncio.to_thread(
                flm_server.check_flm_update,
                _FLM_UPDATE_CACHE,
                force=True,
            )
            self.update_version_info(data)
            try:
                latest = str(data.get("latest") or "").strip()
                has_update = bool(data.get("has_update", False))
                status = self.query_one("#flm-update-status", Static)
                if latest and has_update:
                    status.update(
                        f"[yellow]FLM {latest} available, "
                        f"rebuild for updated runtime.[/]"
                    )
                    status.add_class("-visible")
                    self._schedule_status_hide()
                elif latest and not has_update:
                    status.update("[yellow]FLM up to date ✓[/]")
                    status.add_class("-visible")
                    self._schedule_status_hide()
            except Exception as exc:
                log.warning("update check status update failed: %s", exc)
        except Exception as exc:
            try:
                status = self.query_one("#flm-update-status", Static)
                status.update(f"[red]Update check error: {exc}[/]")
                status.add_class("-visible")
                self._schedule_status_hide()
            except Exception as inner:
                log.warning("error status display failed after outer exception: %s", inner)
        finally:
            if icon is not None:
                try:
                    icon.update("🗘 ")
                except Exception as exc:
                    log.warning("could not restore check-update icon: %s", exc)
