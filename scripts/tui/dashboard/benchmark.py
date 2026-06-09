from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static

from tui.dashboard._daemon import _daemon_post, _resolve_result
from tui.dashboard._pane import Pane


class BenchmarkPane(Pane):
    """Benchmark pane: run + results table."""

    def __init__(self) -> None:
        super().__init__()
        self._data: dict = {}

    def compose(self) -> ComposeResult:
        yield Static("Loading Benchmark…", id="bench-content", classes="panel-content")

    def _fetch(self) -> None:
        resp = _daemon_post("bench_history")
        if resp.get("ok"):
            self._data = resp.get("result") or {}
        else:
            self._data = {"_error": resp.get("error", "daemon unreachable")}
        self.call_later(self._on_data)

    def _on_data(self) -> None:
        content = self.query_one("#bench-content", Static)
        d = self._data

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

    # ------------------------------------------------------------------
    # Actions  (currently unused — wired externally when available)
    # ------------------------------------------------------------------

    def _trigger_bench(self) -> None:
        """Run benchmark for current model."""
        cfg_resp = _daemon_post("config_snapshot")
        model = ""
        if cfg_resp.get("ok"):
            model = str((cfg_resp.get("result") or {}).get("flm_config", {}).get("active_model", ""))
        if model:
            resp = _daemon_post("bench_start", {"model": model})
            result = _resolve_result(resp)
            self.fetch()
            from textual import log as tlog
            tlog(f"Benchmark started: {result}")
