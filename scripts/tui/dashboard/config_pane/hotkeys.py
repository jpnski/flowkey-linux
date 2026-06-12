"""Hotkeys panel — interactive modifier + letter selectors for global hotkeys."""

from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Click
from textual.widgets import Input, Static

from tui.dashboard._daemon import _daemon_post

log = logging.getLogger("flowkey.tui.dashboard")

# (label, config_key)
_HOTKEY_ACTIONS: list[tuple[str, str]] = [
    ("Ask model",    "ask_chat"),
    ("Grammar fix",  "grammar_fix"),
    ("Capture note", "capture_note"),
]

_MOD_OPTIONS: list[str] = ["ctrl", "super", "alt"]

# Build widget ID -> (action, slot) lookup.
_WIDGET_MAP: dict[str, tuple[str, int]] = {}
for _label, action in _HOTKEY_ACTIONS:
    _WIDGET_MAP[f"hk-{action}-mod1"] = (action, 1)
    _WIDGET_MAP[f"hk-{action}-mod2"] = (action, 2)


def _parse_hotkey(raw: str) -> tuple[str, str, str]:
    """Parse a hotkey string into (mod1, mod2, letter).

    Accepts both human-readable ('ctrl+alt+g') and legacy compact ('^!g')
    formats.  Returns ('ctrl', 'ctrl', '') as fallback.
    """
    s = (raw or "").strip().lower()
    if "+" in s:
        parts = [p.strip() for p in s.split("+") if p.strip()]
        known_mods = {"ctrl", "super", "alt"}
        mods: list[str] = []
        letter = ""
        for p in parts:
            if p in known_mods:
                mods.append(p)
            elif p.isalnum() and not letter:
                letter = p
        mod1 = mods[0] if len(mods) > 0 else "ctrl"
        mod2 = mods[1] if len(mods) > 1 else mods[0] if len(mods) == 1 else "ctrl"
        return (mod1, mod2, letter)

    # Legacy compact format.
    SYM_TO_MOD = {"^": "ctrl", "!": "alt", "#": "super"}
    mods = []
    letter = ""
    for ch in s:
        if ch in SYM_TO_MOD:
            mods.append(SYM_TO_MOD[ch])
        elif ch.isalnum() and not letter:
            letter = ch
    mod1 = mods[0] if len(mods) > 0 else "ctrl"
    mod2 = mods[1] if len(mods) > 1 else mods[0] if len(mods) == 1 else "ctrl"
    return (mod1, mod2, letter)


def _format_hotkey(mod1: str, mod2: str, letter: str) -> str:
    """Build a human-readable hotkey string from modifier names and letter."""
    parts = [mod1, mod2]
    if letter:
        parts.append(letter.lower())
    return "+".join(parts)


class HotkeysPanel(Vertical):
    """Interactive hotkey editor — three columns, each with mod1/mod2 + letter.

    Columns: Ask model | Grammar fix | Capture note
    Each column has a label and an input row:
      [mod1] [mod2] [letter]
    where mod1/mod2 cycle through ctrl / super / alt on click.

    Mod changes are saved immediately.  Letter changes are saved when the
    user presses Enter in the letter input.
    """

    DEFAULT_CSS = """
    HotkeysPanel {
        height: auto;
        border: solid $surface;
        padding: 0 1;
        margin: 0;
    }
    .hk-cols {
        height: auto;
    }
    .hk-col {
        width: 33.33%;
        height: auto;
        padding: 0 1;
    }
    .hk-label {
        color: $text-muted;
        margin-top: 1;
        margin-bottom: 0;
    }
    .hk-input-row {
        height: 3;
        align: left middle;
    }
    .hk-mod {
        width: 8;
        height: 3;
        border: tall $border-blurred;
        background: $surface;
        content-align: center middle;
        color: $text;
        margin-right: 1;
    }
    .hk-mod:hover {
        background: $accent;
    }
    .hk-letter {
        width: 8;
    }
    HotkeysPanel > .panel-header {
        margin-top: 1;
        margin-bottom: 0;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._values: dict[str, tuple[str, str, str]] = {}
        # Letters the user has intentionally set — never overwritten by refresh.
        self._user_letters: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        yield Static("Hotkeys", classes="panel-header")
        with Horizontal(classes="hk-cols"):
            for label, action in _HOTKEY_ACTIONS:
                with Vertical(classes="hk-col"):
                    yield Static(label, classes="hk-label")
                    with Horizontal(classes="hk-input-row"):
                        yield Static("ctrl", id=f"hk-{action}-mod1", classes="hk-mod")
                        yield Static("ctrl", id=f"hk-{action}-mod2", classes="hk-mod")
                        yield Input(
                            value="",
                            id=f"hk-{action}-letter",
                            classes="hk-letter",
                        )

    # ---- Data ingestion (called by ConfigPane) ----

    def update_hotkeys(self, hotkeys: dict[str, str]) -> None:
        """Populate the three hotkey rows from a {action: hotkey_string} dict.

        Preserves letters the user has explicitly set (stored in _user_letters).
        The periodic ConfigPane refresh will NOT overwrite user-set letters.
        """
        for _label, action in _HOTKEY_ACTIONS:
            raw = hotkeys.get(action, "")
            mod1, mod2, letter = _parse_hotkey(raw)
            # Preserve user-set letter.
            if action in self._user_letters:
                letter = self._user_letters[action]
            self._values[action] = (mod1, mod2, letter)
            try:
                self.query_one(f"#hk-{action}-mod1", Static).update(mod1)
                self.query_one(f"#hk-{action}-mod2", Static).update(mod2)
                inp = self.query_one(f"#hk-{action}-letter", Input)
                inp.value = letter
            except Exception as exc:
                log.warning("could not update hotkey display for %s: %s", action, exc)

    # ---- Event handlers ----

    def on_click(self, event: Click) -> None:
        """Cycle a modifier on click — instant UI, fire-and-forget daemon save."""
        wid = str(event.widget.id or "")
        match = _WIDGET_MAP.get(wid)
        if match is None:
            return
        action, slot = match

        mod1, mod2, letter = self._values.get(action, ("ctrl", "ctrl", ""))
        old_hotkey = _format_hotkey(mod1, mod2, letter)
        current = mod1 if slot == 1 else mod2
        idx = _MOD_OPTIONS.index(current) if current in _MOD_OPTIONS else 0
        next_idx = (idx + 1) % len(_MOD_OPTIONS)
        new_mod = _MOD_OPTIONS[next_idx]

        if slot == 1:
            mod1 = new_mod
        else:
            mod2 = new_mod
        self._values[action] = (mod1, mod2, letter)

        try:
            event.widget.update(new_mod)
        except Exception as exc:
            log.warning("could not update hotkey widget: %s", exc)

        # Persist with correct old value for revert-on-failure.
        hotkey_str = _format_hotkey(mod1, mod2, letter)
        self.run_worker(
            partial(self._do_save, action, hotkey_str, old_hotkey),
            exclusive=True,
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Save hotkey when the user presses Enter in the letter input."""
        input_id = str(event.input.id or "")
        if not input_id.endswith("-letter"):
            return
        action = input_id[3:-7]
        if action not in {a for _, a in _HOTKEY_ACTIONS}:
            return

        raw = event.value.strip().lower()
        filtered = "".join(ch for ch in raw if ch.isalnum())[:1]
        if filtered:
            event.input.value = filtered
        else:
            return

        mod1, mod2, _letter = self._values.get(action, ("ctrl", "ctrl", ""))
        if filtered == _letter:
            return  # unchanged
        self._values[action] = (mod1, mod2, filtered)
        self._user_letters[action] = filtered

        hotkey_str = _format_hotkey(mod1, mod2, filtered)
        old_hotkey = _format_hotkey(mod1, mod2, _letter)
        self.run_worker(
            partial(self._do_save, action, hotkey_str, old_hotkey),
            exclusive=True,
        )

    # ---- Persist ----

    async def _do_save(self, action: str, hotkey_str: str, old_hotkey: str) -> None:
        try:
            resp = await asyncio.to_thread(
                _daemon_post, "apply_config_patch",
                {"patch": {"hotkeys": {action: hotkey_str}}},
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
                from tui.dashboard import DashboardWidget
                self.app.query_one(DashboardWidget).refresh_now()
            except Exception as exc:
                log.warning("could not refresh dashboard after hotkey save: %s", exc)
        else:
            mod1, mod2, letter = _parse_hotkey(old_hotkey)
            self._values[action] = (mod1, mod2, letter)
            try:
                self.query_one(f"#hk-{action}-mod1", Static).update(mod1)
                self.query_one(f"#hk-{action}-mod2", Static).update(mod2)
                inp = self.query_one(f"#hk-{action}-letter", Input)
                inp.value = letter
            except Exception as exc:
                log.warning("could not revert hotkey display after error: %s", exc)
            self.app.notify(
                f"Failed to update: {resp.get('error', 'unknown')}",
                severity="error", timeout=5,
            )
