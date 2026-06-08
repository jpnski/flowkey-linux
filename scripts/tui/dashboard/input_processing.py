"""Input Processing panel — displays current input processing config."""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static


class InputProcessingPanel(Vertical):
    """Shows the current input-processing (chunking) configuration."""

    DEFAULT_CSS = """
    InputProcessingPanel {
        height: auto;
        border: solid $surface;
        padding: 0 1;
        margin: 0;
    }
    #input-processing-content {
        height: auto;
        margin-top: 1;
        margin-bottom: 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("Input Processing", classes="panel-header")
        yield Static("", id="input-processing-content")

    def update_config(self, cfg: dict[str, Any]) -> None:
        """Populate the content area from the input_processing config dict."""
        try:
            content = self.query_one("#input-processing-content", Static)
            enabled = bool(cfg.get("enabled", True))
            threshold = cfg.get("long_threshold_chars", 1400)
            chunk_size = cfg.get("chunk_size_chars", 1200)
            min_chunk = cfg.get("min_chunk_chars", 700)
            lines = [
                f"  Enabled:        [bold]{enabled}[/]",
                f"  Long threshold: [bold]{threshold}[/] chars",
                f"  Chunk size:     [bold]{chunk_size}[/] chars",
                f"  Min chunk:      [bold]{min_chunk}[/] chars",
            ]
            content.update("\n".join(lines))
        except Exception:
            pass
