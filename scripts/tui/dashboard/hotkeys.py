"""Hotkeys panel — displays current keyboard shortcut bindings."""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static


class HotkeysPanel(Vertical):
    """Displays hotkey bindings from the daemon config."""

    DEFAULT_CSS = """
    HotkeysPanel {
        height: auto;
        border: solid $surface;
        padding: 0 1;
        margin: 0;
    }
    #hotkeys-content {
        height: auto;
        margin-top: 1;
        margin-bottom: 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("Hotkeys", classes="panel-header")
        yield Static("", id="hotkeys-content")

    def update_hotkeys(self, hotkeys: dict[str, str]) -> None:
        """Populate the hotkey list from a {action: key} dict."""
        try:
            content = self.query_one("#hotkeys-content", Static)
            if not hotkeys:
                content.update("[dim]No hotkeys configured[/]")
                return
            lines: list[str] = []
            for action, key in sorted(hotkeys.items()):
                lines.append(f"  {action}: [bold]{key}[/]")
            content.update("\n".join(lines))
        except Exception:
            pass
