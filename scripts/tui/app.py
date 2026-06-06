"""Flowkey Textual TUI — main application.

Tabbed interface with Chat (primary) and Dashboard panels.
Connects to the daemon at 127.0.0.1:52650 for data and actions.

Usage:
    flowkey-tui [--parent-pid N]

Keyboard:
    Ctrl+1      Chat tab
    Ctrl+2      Dashboard tab
    Ctrl+P      Command palette
    Ctrl+Q      Quit
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header, TabbedContent, TabPane

from tui.chat import ChatWidget
from tui.dashboard import DashboardWidget

log = logging.getLogger("flowkey.tui.app")

DAEMON_BASE_URL = "http://127.0.0.1:52650"

# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

APP_CSS = """
Screen {
    background: $surface;
}

TabbedContent {
    height: 1fr;
}

TabPane {
    padding: 0;
    height: 1fr;
}

TabPane:focus {
    border: none;
}

Header {
    background: $primary;
    color: $text;
    text-style: bold;
}

Footer {
    background: $panel;
    color: $text-muted;
}

/* Dashboard-specific fixes for tab panel scrolling */
#dashboard-tabs {
    height: 100%;
}

#dashboard-tabs > TabPane {
    height: 100%;
}
"""


# ---------------------------------------------------------------------------
# Main TUI screen
# ---------------------------------------------------------------------------


class FlowkeyScreen(Screen):
    """Main screen with tabbed chat + dashboard."""

    BINDINGS = [
        Binding("ctrl+1", "switch_tab('chat')", "Chat", show=True),
        Binding("ctrl+2", "switch_tab('dashboard')", "Dashboard", show=True),
        Binding("ctrl+q", "quit", "Quit", show=True),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="chat"):
            with TabPane("💬 Chat", id="chat"):
                yield ChatWidget()
            with TabPane("📊 Dashboard", id="dashboard"):
                yield DashboardWidget()
        yield Footer()

    def action_switch_tab(self, tab: str) -> None:
        """Switch to the given tab by ID."""
        tabbed = self.query_one(TabbedContent)
        tabbed.active = tab

    def action_quit(self) -> None:
        self.app.exit()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


class FlowkeyTUI(App):
    """Flowkey Textual TUI application."""

    TITLE = "Flowkey TUI"
    CSS = APP_CSS
    SCREENS = {"main": FlowkeyScreen}
    BINDINGS = [
        Binding("ctrl+p", "command_palette", "Command palette", show=True),
    ]

    def __init__(self, parent_pid: int = 0) -> None:
        super().__init__()
        self._parent_pid = parent_pid
        self._shutdown_event = threading.Event()

    def on_mount(self) -> None:
        self.push_screen("main")

        # Start parent-PID watcher
        if self._parent_pid > 0:
            threading.Thread(
                target=self._watch_parent,
                args=(self._parent_pid,),
                daemon=True,
            ).start()

    def _watch_parent(self, parent_pid: int) -> None:
        """Exit when parent process disappears."""
        while not self._shutdown_event.is_set():
            self._shutdown_event.wait(5.0)
            if self._shutdown_event.is_set():
                return
            try:
                if not os.path.exists(f"/proc/{parent_pid}/status"):
                    log.info("parent PID %d gone, exiting TUI", parent_pid)
                    self.call_from_thread(self.exit)
                    return
            except OSError:
                pass

    def action_command_palette(self) -> None:
        """Open the Textual command palette."""
        self.action_command_palette()  # defer to built-in

    def _on_signal(self, signum: int, _frame) -> None:
        log.info("received signal %d, exiting TUI", signum)
        self.exit()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    """TUI entry point registered as flowkey-tui console script."""
    parser = argparse.ArgumentParser(description="Flowkey Textual TUI")
    parser.add_argument(
        "--parent-pid", type=int, default=0,
        help="Exit when this PID disappears (0 = no parent watch)",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Register signal handlers
    app = FlowkeyTUI(parent_pid=args.parent_pid)

    def _signal_handler(signum, frame):
        app._on_signal(signum, frame)

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    try:
        app.run()
    except KeyboardInterrupt:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
