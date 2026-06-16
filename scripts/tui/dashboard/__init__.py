"""Textual dashboard for ffchat.

Single-panel dashboard composing independent pane widgets,
each responsible for its own data-fetching and rendering.
"""

from __future__ import annotations

import logging
import threading

from textual.app import ComposeResult
from textual.containers import Vertical

from tui.dashboard._pane import Pane
from tui.dashboard.config_pane import ConfigPane

log = logging.getLogger("ffchat.tui.dashboard")

REFRESH_INTERVAL: float = 10.0


class DashboardWidget(Vertical):
    """Dashboard composing pane widgets.

    Acts as a thin coordinator: composes pane widgets and delegates
    data-fetching / rendering to each.
    """

    DEFAULT_CSS = """
    DashboardWidget {
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
    """

    def compose(self) -> ComposeResult:
        yield ConfigPane()

    def on_mount(self) -> None:
        # First load: synchronous so data appears immediately.
        self._fetch_all_sync()
        # Then poll in background.
        self.set_interval(REFRESH_INTERVAL, self._refresh_all_async)

    # ---- Refresh coordination ----

    def _fetch_all_sync(self) -> None:
        """Fetch all pane data synchronously (called once on mount)."""
        for pane in self.query(Pane):
            pane.fetch()

    def _refresh_all_async(self) -> None:
        """Fetch all pane data in background threads (periodic poll)."""
        for pane in self.query(Pane):
            threading.Thread(target=pane.fetch, daemon=True).start()

    def refresh_now(self) -> None:
        """Public re-fetch hook for child widgets (e.g. FlmModelPanel).

        Synchronously re-fetches and re-renders every pane.
        """
        for pane in self.query(Pane):
            pane.refresh_now()
