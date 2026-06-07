"""Textual dashboard for Flowkey.

Replaces dashboard.py with a multi-panel terminal UI.

Panels:
  - Overview: daemon status, model, version, counters
  - Telemetry: latency percentiles, tokens, tok/s
  - History: recent entries from JSONL
  - Notes: vault info
  - Config: hotkeys, model, performance mode
  - Benchmark: run + results table

All data fetched from the daemon at 127.0.0.1:52650.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from functools import partial
from typing import Any

import loopback_http
import paths as _paths
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widgets import (
    Button,
    Collapsible,
    ListItem,
    ListView,
    ProgressBar,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

log = logging.getLogger("flowkey.tui.dashboard")

DAEMON_BASE_URL = "http://127.0.0.1:52650"
REFRESH_INTERVAL = 10.0  # seconds between auto-refresh


class ModelListItem(ListItem):
    """ListItem that carries a model name. Avoids invalid DOM ids for `gemma4-it:e4b`."""

    def __init__(self, model_name: str) -> None:
        super().__init__(Static(model_name), id=f"flm-dl-{_safe_id(model_name)}")
        self.model_name = model_name


def _safe_id(name: str) -> str:
    """Encode a string so it is a valid Textual DOM identifier (no colons, etc.)."""
    return "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in name)


# ---------------------------------------------------------------------------
# Data fetcher helpers
# ---------------------------------------------------------------------------

_DAEMON_TIMEOUT_DEFAULT = 5.0
_DAEMON_TIMEOUT_MODEL_CHANGE = 75.0  # Must accommodate: stop_flm (~3s) + start_flm port-poll (up to 25s) + warmup request (up to 30s) + buffer
_DAEMON_TIMEOUT_PULL_START = 25.0
_DAEMON_TIMEOUT_PULL_CANCEL = 10.0


def _daemon_post(action: str, args: dict | None = None, *, timeout: float = _DAEMON_TIMEOUT_DEFAULT) -> dict:
    """POST to daemon action and return parsed response."""
    try:
        return loopback_http.json_post(
            f"{DAEMON_BASE_URL}/action/{action}",
            {"args": args or {}},
            headers=loopback_http.daemon_headers(),
            timeout=timeout,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _resolve_result(resp: dict) -> Any:
    """Extract result from daemon response, or error string."""
    if resp.get("ok"):
        return resp.get("result")
    return f"Error: {resp.get('error', 'unknown')}"


# ---------------------------------------------------------------------------
# FLM Model panel (top of Config tab)
# ---------------------------------------------------------------------------


_RESTART_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class FlmModelPanel(Vertical):
    """Interactive FLM model block for the Config tab.

    Provides:
    - A `Select` to switch the active model (lists installed models).
    - A `Button` that toggles a `Collapsible` containing a `ListView` of
      not-yet-installed models. Pressing Enter on a row starts a pull.
    - A `ProgressBar` + status line for in-flight pull progress.
    - A second indeterminate `ProgressBar` + spinner while the FLM server
      is being restarted on an active-model change.
    """

    DEFAULT_CSS = """
    FlmModelPanel {
        height: auto;
        border: solid $surface;
        padding: 0 1;
        margin: 0 0 1 0;
    }
    .flm-section-label {
        color: $text-muted;
        text-style: italic;
        margin-top: 1;
    }
    #flm-active-model-select { margin-bottom: 1; }
    #flm-pull-status-line { color: $text-muted; margin-top: 1; }
    #flm-pull-progress { display: none; }
    #flm-restart-progress { display: none; }
    #flm-cancel-pull-btn { display: none; }
    #flm-pull-progress.active,
    #flm-restart-progress.active,
    #flm-cancel-pull-btn.active { display: block; }
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
        self._pull_in_flight: bool = False
        self._list_populated: bool = False
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

    def compose(self) -> ComposeResult:
        yield Static("FLM Model", classes="panel-header")
        yield Static("Active model", classes="flm-section-label")
        yield Select(
            options=[("(no model loaded)", "")],
            value="",
            allow_blank=False,
            prompt="Choose installed model…",
            id="flm-active-model-select",
            disabled=True,
        )
        yield ProgressBar(total=None, show_eta=False, id="flm-restart-progress")
        yield Static("", id="flm-restart-status-line", classes="flm-section-label")
        yield Static("Download a model", classes="flm-section-label")
        yield Collapsible(
            Vertical(
                Static("", id="flm-empty-download-msg"),
                ListView(id="flm-download-list"),
            ),
            title="Download a model",
            collapsed=True,
            id="flm-download-collapse",
        )
        yield Static("", id="flm-pull-status-line")
        yield ProgressBar(total=100, show_eta=False, id="flm-pull-progress")
        yield Button("Cancel pull", id="flm-cancel-pull-btn")

    def on_mount(self) -> None:
        self.set_interval(1.0, self._refresh_pull_status)
        self.set_interval(0.15, self._tick_restart_spinner)

    # ---- Data ingestion (called by DashboardWidget) ----

    def update_models(self, installed: list[str], not_installed: list[str],
                      active: str, model_loaded: bool = False,
                      *, daemon_reachable: bool = True) -> None:
        """Refresh installed/not-installed lists and active model.

        Idempotent: only re-populates the Select / ListView when something
        actually changed. Preserves user focus on the Select where possible.
        """
        self._daemon_reachable = daemon_reachable
        self._installed_models = list(installed)
        self._not_installed_models = list(not_installed)
        self._model_loaded = model_loaded
        self._active_model = active if model_loaded else ""

        self._refresh_select()
        if not self._list_populated:
            self._refresh_download_list()
            self._list_populated = True

    def mark_daemon_down(self) -> None:
        """Render the panel into its unreachable state."""
        self._daemon_reachable = False
        self._active_model = ""
        self._installed_models = []
        self._not_installed_models = []
        self._list_populated = False
        status = self.query_one("#flm-pull-status-line", Static)
        status.update("[red]Daemon unreachable — cannot list models[/]")
        self._set_select_enabled(False)
        try:
            self.query_one("#flm-cancel-pull-btn", Button).remove_class("active")
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

    def _refresh_download_list(self) -> None:
        list_view = self.query_one("#flm-download-list", ListView)
        list_view.clear()
        for name in self._not_installed_models:
            list_view.append(ModelListItem(name))
        # Update the empty-list message inside the Collapsible.
        empty_msg = self.query_one("#flm-empty-download-msg", Static)
        if not self._not_installed_models:
            empty_msg.update("[dim]All available models are already installed.[/]")
        else:
            empty_msg.update("")

    def _set_select_enabled(self, enabled: bool) -> None:
        try:
            select = self.query_one("#flm-active-model-select", Select)
            select.disabled = not enabled
        except Exception:
            pass

    # ---- Pollers ----

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

        progress = self.query_one("#flm-pull-progress", ProgressBar)
        status = self.query_one("#flm-pull-status-line", Static)
        cancel_btn = self.query_one("#flm-cancel-pull-btn", Button)

        if state == "running":
            progress.add_class("active")
            progress.update(progress=percent)
            cancel_btn.add_class("active")
            status.update(f"Pulling [bold]{model}[/]: {percent:.1f}% — {message or 'starting…'}")
            self._pull_in_flight = True
        elif state == "done":
            progress.add_class("active")
            progress.update(progress=100.0)
            cancel_btn.remove_class("active")
            status.update(f"[green]✓ Pulled {model}[/]")
            self._pull_in_flight = False
            self._list_populated = False
            self._refresh_download_list()
            self._list_populated = True
        elif state == "cancelled":
            progress.remove_class("active")
            cancel_btn.remove_class("active")
            status.update("[yellow]⊘ Pull cancelled[/]")
            self._pull_in_flight = False
        elif state == "error":
            progress.remove_class("active")
            cancel_btn.remove_class("active")
            status.update(f"[red]✗ Pull failed: {error or 'unknown error'}[/]")
            self._pull_in_flight = False
        else:  # idle
            if self._pull_in_flight:
                self._pull_in_flight = False
            cancel_btn.remove_class("active")
            if not self.restarting and not self._not_installed_models and not self._installed_models:
                pass  # leave the "unreachable" / "all installed" message in place

    def _tick_restart_spinner(self) -> None:
        if not self.restarting:
            return
        self._spinner_index = (self._spinner_index + 1) % len(_RESTART_SPINNER)
        glyph = _RESTART_SPINNER[self._spinner_index]
        try:
            line = self.query_one("#flm-restart-status-line", Static)
            line.update(f"[yellow]{glyph} {self.restart_label}[/]")
        except Exception:
            pass

    # ---- Event handlers ----

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "flm-active-model-select":
            return
        new_value = str(event.value or "")

        # Suppress spurious Select.Changed triggered by set_options() in
        # _refresh_select or the revert path.  These fire within the same
        # event-loop iteration as the programmatic mutation, always < 1 s.
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

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "flm-cancel-pull-btn":
            self.run_worker(self._cancel_pull, exclusive=True)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id != "flm-download-list":
            return
        item = event.item
        if not isinstance(item, ModelListItem):
            return
        model = item.model_name
        if not model:
            return
        if self._pull_in_flight:
            self.app.notify("A pull is already in progress", severity="warning", timeout=4)
            return
        self.run_worker(partial(self._start_pull, model), exclusive=True)

    # ---- Workers ----

    async def _apply_model_change(self, new_value: str) -> None:
        self._set_select_enabled(False)
        self.restarting = True
        self.restart_label = f"Restarting FLM server on {new_value}…"
        self.query_one("#flm-restart-progress", ProgressBar).add_class("active")
        self.query_one("#flm-restart-status-line", Static).update(
            f"[yellow]{_RESTART_SPINNER[0]} {self.restart_label}[/]"
        )

        try:
            resp = await asyncio.to_thread(
                _daemon_post, "apply_config_patch", {"patch": {"flm_model": new_value}},
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
            self.query_one("#flm-restart-progress", ProgressBar).remove_class("active")
            self.query_one("#flm-restart-status-line", Static).update("")
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
            self.app.notify("Pull cancelled", severity="information", timeout=4)
        except Exception as exc:
            self.app.notify(
                f"Cancel failed: {exc}", severity="error", timeout=6
            )


# ---------------------------------------------------------------------------
# Dashboard main widget
# ---------------------------------------------------------------------------


class DashboardWidget(Vertical):
    """Multi-panel dashboard with tabs."""

    DEFAULT_CSS = """
    DashboardWidget {
        height: 100%;
    }

    #dashboard-tabs {
        height: 100%;
    }

    .panel-header {
        text-style: bold;
        padding: 0 1;
        background: $panel;
        color: $text;
        height: 1;
    }

    .panel-content {
        padding: 0 1;
    }

    .metric-grid {
        layout: grid;
        grid-size: 2;
        grid-gutter: 1;
        padding: 0 1;
    }

    .metric-card {
        border: solid $surface;
        padding: 1;
        height: auto;
    }

    .metric-label {
        color: $text-muted;
        text-style: italic;
    }

    .metric-value {
        text-style: bold;
        color: $primary;
    }

    .action-bar {
        height: 3;
        padding: 0 1;
        align: center middle;
    }

    .action-bar > Button {
        margin: 0 1;
    }

    DataTable {
        height: auto;
        max-height: 20;
    }

    #bench-result-table {
        height: auto;
        max-height: 20;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._overview_data: dict = {}
        self._stats_data: dict = {}
        self._history_data: list[dict] = []
        self._notes_data: dict = {}
        self._config_data: dict = {}
        self._bench_data: dict = {}
        self._models_data: dict = {}
        self._loading = True

    def compose(self) -> ComposeResult:
        with TabbedContent(id="dashboard-tabs"):
            with TabPane("Overview", id="tab-overview"):
                yield Static("Loading Overview...", id="overview-content", classes="panel-content")
            with TabPane("Telemetry", id="tab-telemetry"):
                yield Static("Loading Telemetry...", id="telemetry-content", classes="panel-content")
            with TabPane("History", id="tab-history"):
                yield Static("Loading History...", id="history-content", classes="panel-content")
            with TabPane("Notes", id="tab-notes"):
                yield Static("Loading Notes...", id="notes-content", classes="panel-content")
            with TabPane("Config", id="tab-config"):
                with Vertical(id="config-tab-root"):
                    yield FlmModelPanel(id="flm-model-panel")
                    yield Static("Loading Config...", id="config-content", classes="panel-content")
            with TabPane("Benchmark", id="tab-bench"):
                yield Static("Loading Benchmark...", id="bench-content", classes="panel-content")

    def on_mount(self) -> None:
        # First load: synchronous so data appears immediately.
        self._fetch_all_sync()
        # Then poll in background.
        self.set_interval(REFRESH_INTERVAL, self._refresh_all_async)

    # ---- Refresh ----

    def _fetch_all_sync(self) -> None:
        """Fetch all data synchronously (called once on mount)."""
        self._fetch_overview()
        self._fetch_telemetry()
        self._fetch_history()
        self._fetch_notes()
        self._fetch_config()
        self._fetch_bench()
        self._fetch_models()

    def _refresh_all_async(self) -> None:
        """Fetch all dashboard data in background threads (periodic poll)."""
        threading.Thread(target=self._fetch_overview, daemon=True).start()
        threading.Thread(target=self._fetch_telemetry, daemon=True).start()
        threading.Thread(target=self._fetch_history, daemon=True).start()
        threading.Thread(target=self._fetch_notes, daemon=True).start()
        threading.Thread(target=self._fetch_config, daemon=True).start()
        threading.Thread(target=self._fetch_bench, daemon=True).start()
        threading.Thread(target=self._fetch_models, daemon=True).start()

    def _fetch_overview(self) -> None:
        config_resp = _daemon_post("config_snapshot")
        version_resp = _daemon_post("version")
        status_resp = _daemon_post("status")
        stats_resp = _daemon_post("stats")

        result = {}
        if config_resp.get("ok"):
            result["config"] = config_resp["result"]
        else:
            result["_error"] = config_resp.get("error", "daemon unreachable")
        if version_resp.get("ok"):
            result["version"] = _resolve_result(version_resp)
        if status_resp.get("ok"):
            result["status"] = _resolve_result(status_resp)
        if stats_resp.get("ok"):
            result["stats"] = stats_resp["result"]

        self._overview_data = result
        self.call_later(self._render_overview)

    def _fetch_telemetry(self) -> None:
        resp = _daemon_post("stats")
        if resp.get("ok"):
            self._stats_data = resp.get("result") or {}
        else:
            self._stats_data = {"error": resp.get("error", "daemon unreachable")}
        self.call_later(self._render_telemetry)

    def _fetch_history(self) -> None:
        """Read recent history entries from the JSONL file."""
        history_path = _paths.DATA_DIR / "grammar_fix_history.jsonl"
        entries: list[dict] = []
        if history_path.exists():
            try:
                with history_path.open("r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            except OSError:
                pass
        self._history_data = entries[-50:]  # last 50
        self.call_later(self._render_history)

    def _fetch_notes(self) -> None:
        config_resp = _daemon_post("config_snapshot")
        if config_resp.get("ok"):
            cfg = config_resp["result"]
            self._notes_data = cfg.get("notes", {})
        else:
            self._notes_data = {"_error": config_resp.get("error", "daemon unreachable")}
        self.call_later(self._render_notes)

    def _fetch_config(self) -> None:
        resp = _daemon_post("config_snapshot")
        if resp.get("ok"):
            self._config_data = resp.get("result") or {}
        else:
            self._config_data = {"_error": resp.get("error", "daemon unreachable")}
        self.call_later(self._render_config)

    def _fetch_bench(self) -> None:
        resp = _daemon_post("bench_history")
        if resp.get("ok"):
            self._bench_data = resp.get("result") or {}
        else:
            self._bench_data = {"_error": resp.get("error", "daemon unreachable")}
        self.call_later(self._render_bench)

    def _fetch_models(self) -> None:
        installed_resp = _daemon_post("models_installed")
        not_installed_resp = _daemon_post("models_not_installed")
        config_resp = _daemon_post("config_snapshot")
        active = ""
        installed_names: list[str] = []
        not_installed_names: list[str] = []
        daemon_reachable = True

        if installed_resp.get("ok"):
            installed_names = list((installed_resp.get("result") or {}).get("models") or [])
        else:
            daemon_reachable = False
        if not_installed_resp.get("ok"):
            not_installed_names = list(
                (not_installed_resp.get("result") or {}).get("models") or []
            )
        else:
            daemon_reachable = False
        model_loaded = False
        if config_resp.get("ok"):
            cfg = config_resp.get("result") or {}
            active = str(cfg.get("flm_model") or "")
            model_loaded = bool(cfg.get("flm_model_loaded", False))

        self._models_data = {
            "installed": installed_names,
            "not_installed": not_installed_names,
            "active": active,
            "model_loaded": model_loaded,
        }
        self.call_later(
            self._render_flm_panel,
            installed_names,
            not_installed_names,
            active,
            daemon_reachable,
            model_loaded,
        )

    # ---- Render panels ----

    def _render_overview(self) -> None:
        content = self.query_one("#overview-content", Static)
        d = self._overview_data
        config = d.get("config", {})
        stats = d.get("stats", {})

        if d.get("_error") or not config:
            content.update(f"[red]Daemon unreachable — {d.get('_error', 'no data')}[/]")
            return

        lines = [
            "[bold]Overview[/]",
            "",
            f"Version:   {d.get('version', '?')}",
            f"Status:    {d.get('status', '?')}",
            f"Model:     {config.get('flm_model', '?')}",
            f"Base URL:  {config.get('flm_base_url', '?')}",
            "",
            "[bold]Activity Counters[/]",
            f"Total requests:    {stats.get('total', 0)}",
            f"By mode:           {stats.get('by_mode', {})}",
            f"Avg latency:       {stats.get('avg_latency_seconds', 0):.2f}s",
            "",
            "[bold]Preferences[/]",
            f"Performance mode:  {config.get('server', {}).get('performance_mode', '?')}",
            f"History text:      {'visible' if config.get('history_store_text') else 'redacted'}",
            f"Routing:           {'enabled' if config.get('routing', {}).get('enabled') else 'disabled'}",
            "",
            "[bold]Hotkeys[/]",
        ]
        hotkeys = config.get("hotkeys", {})
        for action, key in sorted(hotkeys.items()):
            lines.append(f"  {action}: {key}")

        hotkey_lines = config.get("hotkeys", {})
        if isinstance(hotkey_lines, dict):
            for action, key in sorted(hotkey_lines.items()):
                lines.append(f"  {action}: {key}")

        content.update("\n".join(lines))

    def _render_telemetry(self) -> None:
        content = self.query_one("#telemetry-content", Static)
        d = self._stats_data

        if not d or "error" in d:
            content.update(f"[red]Telemetry unavailable: {d.get('error', 'no data')}[/]")
            return

        by_mode = d.get("by_mode", {})
        mode_lines = "\n".join(f"  {k}: {v}" for k, v in sorted(by_mode.items()))

        lines = [
            "[bold]Telemetry[/]",
            "",
            f"Total requests:       {d.get('total', 0)}",
            "",
            "[bold]By Mode[/]",
            mode_lines or "  (no data)",
            "",
            "[bold]Latency (seconds)[/]",
            f"  Average:            {d.get('avg_latency_seconds', 0):.3f}",
            f"  P50:                {d.get('p50_latency_seconds', 0):.3f}",
            f"  P95:                {d.get('p95_latency_seconds', 0):.3f}",
            "",
            "[bold]Tokens[/]",
            f"  Prompt tokens:      {d.get('total_prompt_tokens', 0)}",
            f"  Completion tokens:  {d.get('total_completion_tokens', 0)}",
            "",
            "[bold]Speed[/]",
            f"  Avg tok/s:          {d.get('avg_tok_per_sec', 0):.1f}",
            f"  P50 tok/s:          {d.get('p50_tok_per_sec', 0):.1f}",
        ]
        content.update("\n".join(lines))

    def _render_history(self) -> None:
        content = self.query_one("#history-content", Static)
        entries = self._history_data

        if not entries:
            content.update("[dim]No history entries yet.[/]")
            return

        # Show newest first
        lines = [f"[bold]Recent {len(entries)} entries[/]", ""]
        for entry in reversed(entries[-20:]):
            ts = str(entry.get("timestamp") or entry.get("mode") or "?")[:19]
            mode = str(entry.get("mode", "?")).ljust(12)
            elapsed = entry.get("elapsed_seconds")
            elapsed_str = f"{elapsed:.2f}s" if isinstance(elapsed, (int, float)) else "?s"
            tokens = entry.get("prompt_tokens", 0) or 0
            lines.append(f"  {ts}  {mode}  {elapsed_str}  ({tokens} tok)")

        content.update("\n".join(lines))

    def _render_notes(self) -> None:
        content = self.query_one("#notes-content", Static)
        d = self._notes_data

        if d.get("_error"):
            content.update(f"[red]Daemon unreachable — {d['_error']}[/]")
            return

        lines = [
            "[bold]Notes & Vault[/]",
            "",
            f"Vault directory:     {d.get('vault_dir', '?')}",
            f"Categories:          {', '.join(d.get('categories', []) or []) or '(none configured)'}",
            "",
            f"Fetch timeout:       {d.get('fetch_timeout_seconds', 8)}s",
            f"Max extracted:       {d.get('max_extracted_chars', 2000)} chars",
            f"Low conf → inbox:    {d.get('low_confidence_to_inbox', True)}",
            f"Generate title:      {d.get('generate_title', True)}",
            f"Generate summary:    {d.get('generate_summary', True)}",
            "",
            "[dim]Note management and vault browsing available in the TUI chat.[/]",
        ]
        content.update("\n".join(lines))

    def _render_flm_panel(self, installed: list[str], not_installed: list[str],
                          active: str, daemon_reachable: bool,
                          model_loaded: bool = False) -> None:
        try:
            panel = self.query_one(FlmModelPanel)
        except Exception:
            return
        if not daemon_reachable:
            panel.mark_daemon_down()
            return
        panel.update_models(installed, not_installed, active, model_loaded,
                            daemon_reachable=True)

    def _render_config(self) -> None:
        content = self.query_one("#config-content", Static)
        d = self._config_data

        if d.get("_error"):
            content.update(f"[red]Daemon unreachable — {d['_error']}[/]")
            return

        lines = [
            "[bold]Configuration[/]",
            "",
            f"FLM Base URL:       {d.get('flm_base_url', '?')}",
            f"FLM Model:          {d.get('flm_model', '?')}",
            f"Timeout:            {d.get('flm_timeout_seconds', 30)}s",
            "",
            "[bold]Server[/]",
            f"  Auto-start:       {d.get('server', {}).get('auto_start', True)}",
            f"  Performance:      {d.get('server', {}).get('performance_mode', 'balanced')}",
            "",
            "[bold]Routing[/]",
            f"  Enabled:          {d.get('routing', {}).get('enabled', True)}",
            f"  Long threshold:   {d.get('routing', {}).get('long_threshold_chars', 1400)} chars",
            f"  Chunk size:       {d.get('routing', {}).get('chunk_size_chars', 1200)} chars",
            "",
            "[bold]Tone[/]",
            f"  Preset:           {d.get('tone', {}).get('preset', 'formal')}",
            "",
            "[bold]Hotkeys[/]",
        ]
        hotkeys = d.get("hotkeys", {})
        if isinstance(hotkeys, dict):
            for action, key in sorted(hotkeys.items()):
                lines.append(f"  {action}: {key}")

        lines.append("")
        lines.append("[dim]Config editing available via daemon apply_config_patch.[/]")

        content.update("\n".join(lines))

    def _render_bench(self) -> None:
        content = self.query_one("#bench-content", Static)
        d = self._bench_data

        lines = [
            "[bold]Benchmark[/]",
            "",
        ]

        # Show pending/running/status
        bench_status = _daemon_post("bench_status")
        if bench_status.get("ok"):
            status = bench_status.get("result", {})
            if isinstance(status, dict):
                state = status.get("state", "idle")
                if state == "running":
                    lines.append(f"[yellow]Benchmark running: {status.get('model', '?')} "
                                 f"({status.get('progress', '?')})[/]")
                elif state == "done":
                    lines.append("[green]Last benchmark completed.[/]")
                elif state == "error":
                    lines.append(f"[red]Benchmark error: {status.get('error', '?')}[/]")
                else:
                    lines.append("[dim]No benchmark running.[/]")

        lines.append("")
        if not d or "error" in d:
            lines.append("[dim]No benchmark history yet.[/]")
        else:
            results = d.get("results", d.get("history", []))
            if isinstance(results, list) and results:
                lines.append(f"[bold]Last {min(len(results), 10)} results[/]")
                lines.append("")
                for r in results[-10:]:
                    if isinstance(r, dict):
                        model = r.get("model", "?")
                        ts = str(r.get("timestamp", ""))[:16]
                        ttft = r.get("ttft_ms", "?")
                        tps = r.get("tok_per_sec", "?")
                        lines.append(f"  {ts}  {model}  TTFT={ttft}ms  TPS={tps}")
            else:
                lines.append("[dim]No benchmark results yet.[/]")

        lines.append("")
        lines.append("[bold]Run new benchmark[/]")
        lines.append("  [dim]Use the daemon action bench_start with a model name.[/]")
        lines.append("  Example: POST /action/bench_start { \"args\": { \"model\": \"gemma4-it:e4b\" } }")

        content.update("\n".join(lines))

    # ---- Actions ----

    def _trigger_bench(self) -> None:
        """Run benchmark for current model."""
        model = self._config_data.get("flm_model", "")
        if model:
            resp = _daemon_post("bench_start", {"model": model})
            result = _resolve_result(resp)
            self.call_later(self._refresh_all)
            from textual import log as tlog
            tlog(f"Benchmark started: {result}")

    def _refresh_now(self) -> None:
        """Manual refresh triggered by button."""
        self._fetch_all_sync()
        self._refresh_all_async()

    def refresh_now(self) -> None:
        """Public re-fetch hook for child widgets (e.g. FlmModelPanel)."""
        self._fetch_all_sync()
        self._refresh_all_async()
