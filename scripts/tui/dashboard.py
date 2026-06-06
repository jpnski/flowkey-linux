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

import json
import logging
import threading
from typing import Any

import loopback_http
import paths as _paths
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import (
    Static,
    TabbedContent,
    TabPane,
)

log = logging.getLogger("flowkey.tui.dashboard")

DAEMON_BASE_URL = "http://127.0.0.1:52650"
REFRESH_INTERVAL = 10.0  # seconds between auto-refresh

# ---------------------------------------------------------------------------
# Data fetcher helpers
# ---------------------------------------------------------------------------


def _daemon_post(action: str, args: dict | None = None) -> dict:
    """POST to daemon action and return parsed response."""
    try:
        return loopback_http.json_post(
            f"{DAEMON_BASE_URL}/action/{action}",
            {"args": args or {}},
            headers=loopback_http.daemon_headers(),
            timeout=5.0,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _resolve_result(resp: dict) -> Any:
    """Extract result from daemon response, or error string."""
    if resp.get("ok"):
        return resp.get("result")
    return f"Error: {resp.get('error', 'unknown')}"


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
        resp = _daemon_post("models_installed")
        if resp.get("ok"):
            self._models_data = resp.get("result") or {}

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
        self._refresh_all()
