"""FLM Runtime panel — version display + update check for Config tab."""

from __future__ import annotations

import asyncio
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.events import Click
from textual.widgets import Static

from tui.dashboard._daemon import _daemon_post

_DEFAULT_TIMEOUT = 15.0


class FlmRuntimePanel(Vertical):
    """FLM runtime version display and update check.

    Shows the installed ``flm`` version and a button to check GitHub for
    a newer release.  Update-check results are cached server-side (~24h)
    so the initial load is fast and the button triggers a forced refresh.
    """

    DEFAULT_CSS = """
    FlmRuntimePanel {
        height: auto;
        border: solid $surface;
        padding: 0 1;
        margin: 0;
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
        width: auto;
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
    #flm-update-status {
        height: auto;
        display: none;
        margin-top: 1;
    }
    #flm-update-status.-visible {
        display: block;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._current_version: str = ""
        self._latest_version: str = ""
        self._has_update: bool = False
        self._status_timer: Any = None  # set_timer handle, cancelled on new status

    def compose(self) -> ComposeResult:
        yield Static("FLM Runtime", classes="panel-header")
        yield Static("Current version", classes="subsection-header")
        with Vertical(id="flm-version-row"):
            yield Static("[dim](checking…)[/]", id="flm-version-box")
            yield Static("🗘 ", id="flm-check-update-btn")
        yield Static("", id="flm-update-status")

    # ---- Data ingestion (called by ConfigPane) ----

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

    # ---- Event handlers ----

    def on_click(self, event: Click) -> None:
        if event.widget.id == "flm-check-update-btn":
            self.run_worker(self._do_update_check, exclusive=True)

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
