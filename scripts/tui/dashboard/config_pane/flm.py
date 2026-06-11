"""FLM Runtime and Model panel — version info, active model, model download (top of Config tab)."""

from __future__ import annotations

import asyncio
import re
import time
from functools import partial
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Click
from textual.reactive import reactive
from textual.widgets import Select, Static

from tui.dashboard._daemon import (
    _DAEMON_TIMEOUT_MODEL_CHANGE,
    _DAEMON_TIMEOUT_PULL_CANCEL,
    _DAEMON_TIMEOUT_PULL_START,
    _daemon_post,
)

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
        self._pull_completed_key: str = ""  # guards against re-notifying on repeated polls of the same terminal state
        self._pull_last_notify_at: float = 0.0  # monotonic cooldown: prevent toast re-fire within 6s
        self._daemon_reachable: bool = True
        self._last_model_change_at: float = 0.0
        # Whether the FLM server is reachable and the model is confirmed loaded.
        self._model_loaded: bool = False
        # Tracks the last time _refresh_select (or the revert path in
        # _apply_model_change) programmatically mutated the Select widget.
        # Any Select.Changed firing within 1 s of this timestamp is treated
        # as a spurious side-effect of set_options() and suppressed.
        self._last_select_refresh_at: float = 0.0
        # Set of model names last passed to Select.set_options. If unchanged
        # on the next _refresh_select call, set_options is skipped entirely,
        # avoiding the spurious first-option default Select.Changed event.
        self._last_select_options: list[str] = []
        # Same suppression mechanism for the download Select.
        self._last_dl_select_refresh_at: float = 0.0
        # Cached option names — skip set_options when unchanged.
        self._last_dl_select_options: list[str] = []
        # Version / update-check state.
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
        """Refresh installed/not-installed lists and active model.

        Idempotent: only re-populates the Select widgets when something
        actually changed. Preserves user focus on the Select where possible.
        """
        self._daemon_reachable = daemon_reachable
        self._installed_models = list(installed)
        self._not_installed_models = list(not_installed)
        self._model_loaded = model_loaded
        self._active_model = active if model_loaded else ""

        self._refresh_select()
        # Always refresh the download select when the not-installed list
        # changes (pull completed, daemon refresh, etc.).  _refresh_download_select
        # is idempotent and skips set_options when nothing changed.
        self._refresh_download_select()

    def mark_daemon_down(self) -> None:
        """Render the panel into its unreachable state."""
        self._daemon_reachable = False
        self._active_model = ""
        self._installed_models = []
        self._not_installed_models = []
        try:
            row = self.query_one("#flm-pull-row")
            row.remove_class("active")
        except Exception:
            pass
        self._set_select_enabled(False)
        try:
            self.query_one("#flm-download-select", Select).disabled = True
        except Exception:
            pass

    # ---- Internal renderers ----

    def _refresh_select(self) -> None:
        select = self.query_one("#flm-active-model-select", Select)
        if not self._installed_models:
            self._last_select_refresh_at = time.monotonic()
            select.set_options([("(no models installed)", "")])
            self._set_select_enabled(False)
            self._last_select_options = []
            return

        # Always include the persistent "(none)" option at position 0, followed
        # by installed model names.  Users can select "(none)" to explicitly
        # unload the active model from memory.
        options = [("(none)", "")] + [
            (name, name) for name in self._installed_models
        ]

        # Rebuild options only when the installed list changes.  Calling
        # set_options unnecessarily triggers a spurious Select.Changed
        # (suppressed below by _last_select_refresh_at).
        if self._installed_models != self._last_select_options:
            self._last_select_refresh_at = time.monotonic()
            select.set_options(options)
            self._last_select_options = list(self._installed_models)

        if not self._model_loaded:
            # No model loaded — select "(none)".
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
                        f"{name} ⭐" if name in _STARRED_TAGS else name,
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
        except Exception:
            pass

    # ---- Pollers ----

    @staticmethod
    def _format_pull_status(model: str, percent: float, message: str) -> str:
        """Shorten pull status: drop the verbose daemon prefix, keep only size info."""
        size_match = re.search(r'\([^)]+/\s*[^)]+\)', message)
        size_info = f" {size_match.group(0)}" if size_match else ""
        return f" Pulling [bold]{model}[/] | {percent:.1f}%{size_info}"

    def _refresh_pull_status(self) -> None:
        resp = _daemon_post("pull_status")
        if not resp.get("ok"):
            return
        result = resp.get("result") or {}
        state = str(result.get("state") or "idle")
        model = str(result.get("model") or "")
        percent = float(result.get("percent") or 0.0)
        message = str(result.get("message") or "")
        error = str(result.get("error") or "")

        pull_row = self.query_one("#flm-pull-row")
        spinner = self.query_one("#flm-pull-spinner", Static)
        text = self.query_one("#flm-pull-text", Static)

        if state == "running":
            # Reset completion guard so the next terminal state fires once.
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
        except Exception:
            pass

    def _tick_pull_spinner(self) -> None:
        if not self._pull_in_flight:
            return
        self._pull_spinner_index = (self._pull_spinner_index + 1) % len(_SPINNER)
        glyph = _SPINNER[self._pull_spinner_index]
        try:
            spinner = self.query_one("#flm-pull-spinner", Static)
            spinner.update(f"[yellow]{glyph}[/]")
        except Exception:
            pass

    def _refresh_dashboard(self) -> None:
        """Trigger a full dashboard refresh so model lists are re-fetched."""
        try:
            from tui.dashboard import DashboardWidget
            self.app.query_one(DashboardWidget).refresh_now()
        except Exception:
            pass

    # ---- Event handlers ----

    def on_select_changed(self, event: Select.Changed) -> None:
        # ---- Active-model Select ----
        if event.select.id == "flm-active-model-select":
            new_value = str(event.value or "")

            # Suppress spurious Select.Changed triggered by set_options().
            if time.monotonic() - self._last_select_refresh_at < 1.0:
                return

            if not self._installed_models:
                return

            select = event.select

            # Chat-stream guard (applies to both unloading and switching).
            is_streaming = False
            try:
                from tui.chat import ChatWidget  # local import to avoid cycles
                chat = self.app.query_one(ChatWidget)
                if chat.is_streaming():
                    is_streaming = True
            except Exception:
                pass

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
            # Suppress spurious Select.Changed triggered by set_options().
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
            resp = await asyncio.to_thread(
                _daemon_post, "apply_config_patch", {"patch": {"flm_config": {"active_model": new_value}}},
                timeout=_DAEMON_TIMEOUT_MODEL_CHANGE,
            )
            if not resp.get("ok"):
                raise RuntimeError(str(resp.get("error") or "unknown error"))
            self.app.notify(f"Active model: {new_value}", severity="information")
            # Push the new model name to the chat footer FIRST — this is a
            # simple in-process assignment that returns instantly.  Must run
            # before refresh_now() which blocks the event loop for seconds
            # making 11 synchronous HTTP requests while FLM restarts.
            try:
                from tui.chat import ChatWidget
                chat = self.app.query_one(ChatWidget)
                chat.set_model(new_value)
            except Exception:
                pass
            # Refresh the parent's view of the active model and the installed
            # list.  This makes multiple HTTP requests and may take seconds.
            try:
                from tui.dashboard import DashboardWidget  # local import to avoid cycles
                self.app.query_one(DashboardWidget).refresh_now()
            except Exception:
                pass
        except Exception as exc:
            self.app.notify(
                f"Model change failed: {exc}", severity="error", timeout=8
            )
            # Revert the Select visually.
            try:
                select = self.query_one("#flm-active-model-select", Select)
                self._last_select_refresh_at = time.monotonic()
                if self._prior_active:
                    select.value = self._prior_active
                elif self._installed_models:
                    select.value = self._installed_models[0]
            except Exception:
                pass
        finally:
            self.restarting = False
            self.restart_label = ""
            line = self.query_one("#flm-restart-status-line", Static)
            line.remove_class("active")
            line.update("")
            self._set_select_enabled(True)

    async def _unload_model(self) -> None:
        """Stop the FLM server and reset UI state.

        Called when the user explicitly selects (none) from the Select
        dropdown while a model is loaded.
        """
        try:
            resp = await asyncio.to_thread(
                _daemon_post, "stop", {"args": {}}, timeout=5.0,
            )
            if resp.get("ok") and resp.get("result") == "stopped":
                self.app.notify(
                    "Model unloaded from memory",
                    severity="information", timeout=4,
                )
            elif resp.get("ok") and resp.get("result") == "not_running":
                self.app.notify(
                    "No model was loaded",
                    severity="information", timeout=4,
                )
            else:
                self.app.notify(
                    f"Failed to unload: {resp.get('error', 'unknown')}",
                    severity="error", timeout=6,
                )
        except Exception as exc:
            self.app.notify(
                f"Failed to unload: {exc}", severity="error", timeout=6,
            )
            return

        self._model_loaded = False
        self._active_model = ""
        try:
            from tui.chat import ChatWidget
            chat = self.app.query_one(ChatWidget)
            chat.set_model("")
        except Exception:
            pass
        # Suppress the Select.Changed that _refresh_select triggers when it
        # sets select.value = "" — the user *just* picked (none) intentionally.
        self._last_select_refresh_at = time.monotonic()
        self._refresh_select()

    async def _start_pull(self, model: str) -> None:
        self._set_select_enabled(False)
        try:
            resp = await asyncio.to_thread(
                _daemon_post, "pull_start", {"model": model},
                timeout=_DAEMON_TIMEOUT_PULL_START,
            )
            if not resp.get("ok"):
                raise RuntimeError(str(resp.get("error") or "unknown error"))
            self.app.notify(f"Started pulling {model}", severity="information")
        except Exception as exc:
            self.app.notify(
                f"Pull start failed: {exc}", severity="error", timeout=8
            )
        finally:
            self._set_select_enabled(True)

    async def _cancel_pull(self) -> None:
        try:
            resp = await asyncio.to_thread(
                _daemon_post, "pull_cancel", timeout=_DAEMON_TIMEOUT_PULL_CANCEL,
            )
            if not resp.get("ok"):
                raise RuntimeError(str(resp.get("error") or "unknown error"))
            # Immediate visual feedback; the 1s poller will fire the toast
            # once when state transitions to "cancelled" (with guard).
            try:
                text = self.query_one("#flm-pull-text", Static)
                text.update("Cancelling…")
            except Exception:
                pass
        except Exception as exc:
            self.app.notify(
                f"Cancel failed: {exc}", severity="error", timeout=6
            )

    # ---- Version info ----

    def update_version_info(self, data: dict) -> None:
        """Update version display and update status from a flm_update_check result.

        ``data`` is the ``result`` dict returned by the daemon action::

            {"current": "0.9.43", "latest": "0.9.44",
             "has_update": True, "cached": True, "stale": False, …}
        """
        self._current_version = str(data.get("current") or "").strip()
        self._latest_version = str(data.get("latest") or "").strip()
        self._has_update = bool(data.get("has_update", False))

        # -- version box --
        try:
            box = self.query_one("#flm-version-box", Static)
            if self._current_version:
                box.update(self._current_version)
            else:
                box.update("[yellow]not detected[/]")
        except Exception:
            pass

    # ---- Status auto-hide ----

    def _schedule_status_hide(self) -> None:
        """Cancel any pending hide timer and schedule a new one in 5s."""
        if self._status_timer is not None:
            self._status_timer.cancel()
        self._status_timer = self.set_timer(5.0, self._hide_update_status)

    def _hide_update_status(self) -> None:
        """Remove the -visible class from the status line (timer callback)."""
        self._status_timer = None
        try:
            self.query_one("#flm-update-status", Static).remove_class("-visible")
        except Exception:
            pass

    # ---- Workers ----

    async def _do_update_check(self) -> None:
        """Force a live FLM update check against GitHub releases."""
        icon: Static | None = None
        try:
            icon = self.query_one("#flm-check-update-btn", Static)
            icon.update("…")
        except Exception:
            pass

        try:
            resp = await asyncio.to_thread(
                _daemon_post, "flm_update_check", {"force": True},
                timeout=_DEFAULT_TIMEOUT,
            )
            if resp.get("ok"):
                self.update_version_info(resp.get("result") or {})
                # Show status once (auto-hides after 5s).
                try:
                    data = resp.get("result") or {}
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
                except Exception:
                    pass
            else:
                try:
                    status = self.query_one("#flm-update-status", Static)
                    status.update(f"[red]Update check failed: {resp.get('error', 'unknown')}[/]")
                    status.add_class("-visible")
                    self._schedule_status_hide()
                except Exception:
                    pass
        except Exception as exc:
            try:
                status = self.query_one("#flm-update-status", Static)
                status.update(f"[red]Update check error: {exc}[/]")
                status.add_class("-visible")
                self._schedule_status_hide()
            except Exception:
                pass
        finally:
            if icon is not None:
                try:
                    icon.update("🗘 ")
                except Exception:
                    pass
