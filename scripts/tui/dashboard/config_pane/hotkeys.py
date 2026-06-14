"""Hotkeys panel — editable transform and interaction hotkeys."""

from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, Static

from tui.dashboard import DashboardWidget
from tui.dashboard._daemon import _daemon_post

log = logging.getLogger("flowkey.tui.dashboard")

_HOTKEY_GROUPS: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        "transform_hotkeys",
        "Transform hotkeys",
        [
            ("grammar", "Grammar fix"),
            ("prompt", "Prompt fix (Claude)"),
            ("summarize", "Summarize"),
            ("explain", "Explain code/regex/SQL"),
            ("tone", "Tone shift"),
        ],
    ),
    (
        "interaction_hotkeys",
        "Interaction hotkeys",
        [
            ("open_chat", "Open chat"),
            ("ask_chat", "Ask model"),
            ("capture_note", "Capture note"),
        ],
    ),
]


class HotkeysPanel(Vertical):
    """Editable hotkey editor grouped by transform vs interaction actions."""

    DEFAULT_CSS = """
    HotkeysPanel {
        height: auto;
        border: solid $surface;
        padding: 0 1;
        margin: 0;
    }
    HotkeysPanel > .panel-header {
        margin-top: 1;
        margin-bottom: 0;
    }
    .hk-section {
        height: auto;
        margin-top: 1;
    }
    .hk-section-title {
        color: $text-muted;
        text-style: italic;
        margin-top: 0;
        margin-bottom: 0;
    }
    .hk-row {
        height: 3;
        align: left middle;
        margin-top: 0;
    }
    .hk-row-label {
        width: 20;
        color: $text-muted;
        margin-right: 1;
    }
    .hk-input {
        width: 26;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._values: dict[tuple[str, str], str] = {}

    def compose(self) -> ComposeResult:
        yield Static("Hotkeys", classes="panel-header")
        for group, group_label, actions in _HOTKEY_GROUPS:
            with Vertical(classes="hk-section"):
                yield Static(group_label, classes="hk-section-title")
                for action, label in actions:
                    with Horizontal(classes="hk-row"):
                        yield Static(label, classes="hk-row-label")
                        yield Input(value="", id=f"hk-{group}--{action}", classes="hk-input")

    # ---- Data ingestion (called by ConfigPane) ----

    def update_hotkeys(self, transform_hotkeys: dict[str, str], interaction_hotkeys: dict[str, str]) -> None:
        """Populate the editor from the config snapshot."""
        for group, _group_label, actions in _HOTKEY_GROUPS:
            source = transform_hotkeys if group == "transform_hotkeys" else interaction_hotkeys
            for action, _label in actions:
                raw = str(source.get(action, ""))
                self._values[(group, action)] = raw
                try:
                    self.query_one(f"#hk-{group}--{action}", Input).value = raw
                except Exception as exc:
                    log.warning("could not update hotkey display for %s/%s: %s", group, action, exc)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Save hotkey when the user presses Enter in an input field."""
        input_id = str(event.input.id or "")
        if not input_id.startswith("hk-") or "--" not in input_id:
            return
        body = input_id[3:]
        group, action = body.split("--", 1)
        if group not in {g for g, _title, _actions in _HOTKEY_GROUPS}:
            return

        raw = event.value.strip()
        if not raw:
            return

        current = self._values.get((group, action), "")
        if raw == current:
            return  # unchanged

        self._values[(group, action)] = raw
        self.run_worker(
            partial(self._do_save, group, action, raw, current),
            exclusive=True,
        )

    # ---- Persist ----

    async def _do_save(self, group: str, action: str, hotkey_str: str, old_hotkey: str) -> None:
        try:
            resp = await asyncio.to_thread(
                _daemon_post, "apply_config_patch",
                {"patch": {group: {action: hotkey_str}}},
                timeout=2.0,
            )
        except asyncio.CancelledError:
            return  # cancelled by another exclusive worker — ignore

        if resp.get("ok"):
            self.app.notify(
                f"Hotkey {action}: {hotkey_str}",
                severity="information",
            )
            try:
                self.app.query_one(DashboardWidget).refresh_now()
            except Exception as exc:
                log.warning("could not refresh dashboard after hotkey save: %s", exc)
        else:
            self._values[(group, action)] = old_hotkey
            try:
                self.query_one(f"#hk-{group}--{action}", Input).value = old_hotkey
            except Exception as exc:
                log.warning("could not revert hotkey display after error: %s", exc)
            self.app.notify(
                f"Failed to update: {resp.get('error', 'unknown')}",
                severity="error", timeout=5,
            )
