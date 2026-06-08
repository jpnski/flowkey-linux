"""Flowkey Textual TUI — main application.

Tabbed interface with Chat (primary) and Dashboard panels.
Connects to the daemon at 127.0.0.1:52650 for data and actions.

Usage:
    flowkey-tui [--parent-pid N]

Keyboard:
    F1          Chat tab
    F2          Dashboard tab
    Ctrl+P      Command palette (includes theme browser)
    Ctrl+C      Quit (press twice within 3s)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Input, TabbedContent, TabPane

from tui.chat import ChatWidget
from tui.dashboard import DashboardWidget

import config as _config
import paths as _paths

log = logging.getLogger("flowkey.tui.app")

DAEMON_BASE_URL = "http://127.0.0.1:52650"

# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

APP_CSS = """
Screen {
    background: $surface;
}

Widget {
    scrollbar-size-vertical: 1;
    scrollbar-size-horizontal: 1;
}

TabbedContent {
    height: 1fr;
}

Underline {
    display: none;
}

TabPane {
    padding: 0;
    height: 1fr;
}

TabPane:focus {
    border: none;
}

/* Dashboard-specific fixes for tab panel scrolling */
#dashboard-tabs {
    height: 100%;
}

#dashboard-tabs > TabPane {
    height: 100%;
}

/* Reposition notifications from bottom-right (default) to top-right. */
#textual-toastrack {
    dock: top;
    align: right top;
    margin-bottom: 0;
    margin-top: 1;
}

Toast {
    width: 30;
}
"""


# ---------------------------------------------------------------------------
# Main TUI screen
# ---------------------------------------------------------------------------


class FlowkeyScreen(Screen):
    """Main screen with tabbed chat + dashboard."""

    BINDINGS = [
        Binding("f1", "switch_tab('chat')", "Chat", show=True),
        Binding("f2", "switch_tab('dashboard')", "Dashboard", show=True),
        Binding("ctrl+c", "quit", "Quit (2×)", show=True, priority=True),
    ]

    def compose(self) -> ComposeResult:
        with TabbedContent(initial="chat"):
            with TabPane("💬 Chat", id="chat"):
                yield ChatWidget()
            with TabPane("📊 Dashboard", id="dashboard"):
                yield DashboardWidget()

    def action_switch_tab(self, tab: str) -> None:
        """Switch to the given tab by ID."""
        tabbed = self.query_one(TabbedContent)
        tabbed.active = tab

    def action_quit(self) -> None:
        # Opencode auth: first Ctrl+C with text in the chat input clears it;
        # only an empty input (or non-input focus) arms the two-press quit.
        focused = self.app.focused
        if isinstance(focused, Input) and focused.value:
            focused.value = ""
            focused.cursor_position = 0
            return
        self.app.request_quit()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


class FlowkeyTUI(App):
    """Flowkey Textual TUI application."""

    TITLE = "Flowkey TUI"
    CSS = APP_CSS
    SCREENS = {"main": FlowkeyScreen}
    BINDINGS: list[Binding] = []
    # Seconds the user has to press Ctrl+C a second time to confirm quit.
    QUIT_PRESS_WINDOW = 3.0

    def __init__(self, parent_pid: int = 0, ingest_text: str = "") -> None:
        super().__init__()
        self._parent_pid = parent_pid
        self._ingest_text = ingest_text
        self._shutdown_event = threading.Event()
        self._theme_ready = False  # gate: skip save for initial mount
        self._quit_press_count: int = 0
        self._quit_timer: threading.Timer | None = None

    def on_mount(self) -> None:
        # Load persisted theme from config
        try:
            cfg = _config.load_config(_paths.CONFIG_FILE)
            saved = cfg.get("theme")
            if saved and isinstance(saved, str):
                self.theme = saved
        except Exception:
            pass
        self._theme_ready = True

        self.push_screen("main")

        # Ingest text from --ingest-file (sent by daemon via Ctrl+Shift+A)
        if self._ingest_text:
            screen = self.get_screen("main")
            chat = screen.query_one(ChatWidget)
            chat.post_ingested_text(self._ingest_text)

        # Start parent-PID watcher
        if self._parent_pid > 0:
            threading.Thread(
                target=self._watch_parent,
                args=(self._parent_pid,),
                daemon=True,
            ).start()

    def watch_dark(self, dark: bool) -> None:
        """Auto-save theme when user changes it via command palette.

        ``dark`` is a Textual reactive — it fires on any theme change
        (dark ↔ light or switching between two dark themes). We save
        ``self.theme`` to capture the exact theme name.
        """
        if not self._theme_ready:
            return  # skip the initial apply from on_mount
        try:
            theme = self.theme
            cfg = _config.load_config(_paths.CONFIG_FILE)
            cfg["theme"] = theme
            _config.save_config(_paths.CONFIG_FILE, cfg)
            log.info("theme saved: %s", theme)
        except Exception as exc:
            log.warning("failed to persist theme: %s", exc)

    def _watch_parent(self, parent_pid: int) -> None:
        """Exit when parent process disappears."""
        while not self._shutdown_event.is_set():
            self._shutdown_event.wait(5.0)
            if self._shutdown_event.is_set():
                return
            try:
                if not os.path.exists(f"/proc/{parent_pid}/status"):
                    log.info("parent PID %d gone, exiting TUI", parent_pid)
                    self.call_later(self.exit)
                    return
            except OSError:
                pass

    def _on_signal(self, signum: int, _frame) -> None:
        log.info("received signal %d, exiting TUI", signum)
        self.exit()

    def request_quit(self) -> None:
        """Two-press quit: first press shows hint, second within 3s exits."""
        self._quit_press_count += 1
        if self._quit_press_count == 1:
            self._quit_timer = threading.Timer(
                self.QUIT_PRESS_WINDOW, self._reset_quit_counter
            )
            self._quit_timer.daemon = True
            self._quit_timer.start()
            self.notify("Press Ctrl+C again to exit", timeout=self.QUIT_PRESS_WINDOW)
            return
        self._reset_quit_counter()
        self.exit()

    def _reset_quit_counter(self) -> None:
        self._quit_press_count = 0
        if self._quit_timer is not None:
            self._quit_timer.cancel()
            self._quit_timer = None


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
        "--ingest-file", type=str, default="",
        help="Path to a JSON file with ingest payload to send to chat on startup",
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

    # Suppress expected daemon-connection warnings — dashboard polls
    # periodically and "connection refused" is normal when daemon is off.
    logging.getLogger("flowkey.http").setLevel(logging.ERROR)

    # Read ingest payload from file if provided
    ingest_text = ""
    if args.ingest_file:
        ingest_path = Path(args.ingest_file)
        try:
            payload = json.loads(ingest_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                ingest_text = str(payload.get("text") or "")
                source = str(payload.get("source_app") or "")
                if source:
                    ingest_text = f"[From {source}]\n{ingest_text}"
        except Exception as exc:
            log.warning("failed to read ingest file %s: %s", args.ingest_file, exc)
        # Delete the ingest file regardless — it's one-shot
        try:
            ingest_path.unlink()
        except OSError:
            pass

    # SIGINT is routed through Textual's Ctrl+C screen binding (two-press
    # quit). The KeyboardInterrupt raised by any raw SIGINT is caught below.
    app = FlowkeyTUI(parent_pid=args.parent_pid, ingest_text=ingest_text)

    def _signal_handler(signum, frame):
        app._on_signal(signum, frame)

    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        app.run()
    except KeyboardInterrupt:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
