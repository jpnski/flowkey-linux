"""Input Processing panel — interactive integer fields for chunking config."""

from __future__ import annotations

import asyncio
from functools import partial
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Click
from textual.widgets import Input, Static

from tui.dashboard._daemon import _daemon_post

# (label, config_key, min_value, default, max_value)
_PARAMS: list[tuple[str, str, int, int, int]] = [
    ("Input length threshold", "input_length_threshold", 128, 4_000, 100_000),
    ("Min chunk size",         "min_chunk_size",         10,    200,   5_000),
    ("Chunk size",             "chunk_size",             50,    800,  20_000),
]

class InputProcessingPanel(Vertical):
    """Interactive input-processing config editor with integer text fields.

    Three labelled input rows: Input length threshold, Min Chunk Size, Chunk size.
    Each accepts an integer within its [min, max] range.  Press Enter to apply.
    """

    DEFAULT_CSS = """
    InputProcessingPanel {
        height: auto;
        border: solid $surface;
        padding: 0 1;
        margin: 0;
    }
    .ip-columns {
        height: auto;
    }
    .ip-col {
        width: 33.33%;
        height: auto;
        padding: 0 1;
    }
    .ip-label {
        color: $text-muted;
        padding: 0 0;
        margin-bottom: 0;
        margin-top: 1;
    }
    .ip-input-row {
        height: 3;
        align: left middle;
    }
    .ip-input {
        width: 16;
    }
    .ip-reset-btn {
        width: 3;
        height: 3;
        padding: 0 1;
        content-align: center middle;
        color: $text-muted;
    }
    .ip-reset-btn:hover {
        color: $text;
        background: $surface;
    }
    InputProcessingPanel > .panel-header {
        margin-top: 1;
        margin-bottom: 0;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._values: dict[str, int] = {}
        for _label, key, _min, default, _max in _PARAMS:
            self._values[key] = default

    def compose(self) -> ComposeResult:
        yield Static("Input Processing", classes="panel-header")
        with Horizontal(classes="ip-columns"):
            for label, key, _min, default, _max in _PARAMS:
                with Vertical(classes="ip-col"):
                    yield Static(label, classes="ip-label")
                    with Horizontal(classes="ip-input-row"):
                        yield Input(
                            value=str(default),
                            id=f"ip-{key}",
                            classes="ip-input",
                            type="integer",
                        )
                        yield Static("🗘", id=f"ip-reset-{key}", classes="ip-reset-btn")

    # ---- Data ingestion (called by ConfigPane) ----

    def update_config(self, cfg: dict[str, Any]) -> None:
        """Populate input fields from the input_processing config dict."""
        for _label, key, min_val, default, max_val in _PARAMS:
            raw = cfg.get(key)
            if raw is not None:
                try:
                    val = int(raw)
                except (ValueError, TypeError):
                    val = default
                val = max(min_val, min(val, max_val))
                self._values[key] = val
                try:
                    inp = self.query_one(f"#ip-{key}", Input)
                    inp.value = str(val)
                except Exception:
                    pass

    # ---- Event handlers ----

    def on_input_submitted(self, event: Input.Submitted) -> None:
        input_id = str(event.input.id or "")
        if not input_id.startswith("ip-"):
            return
        key = input_id[3:]  # strip "ip-" prefix

        # Find parameter definition.
        param = next((p for p in _PARAMS if p[1] == key), None)
        if param is None:
            return
        _label, _key, min_val, _default, max_val = param

        raw = event.value.strip()
        try:
            val = int(raw)
        except (ValueError, TypeError):
            event.input.value = str(self._values.get(key, ""))
            self.app.notify("Must be an integer", severity="error", timeout=3)
            return

        if val < min_val:
            event.input.value = str(self._values[key])
            self.app.notify(f"Minimum: {min_val}", severity="error", timeout=3)
            return
        if val > max_val:
            event.input.value = str(self._values[key])
            self.app.notify(f"Maximum: {max_val}", severity="error", timeout=3)
            return

        if val == self._values.get(key):
            return  # unchanged — no-op

        self.run_worker(
            partial(self._apply_change, key, val, min_val, max_val),
            exclusive=True,
        )

    # ---- Click handlers ----

    def on_click(self, event: Click) -> None:
        widget_id = str(event.widget.id or "")
        if not widget_id.startswith("ip-reset-"):
            return
        key = widget_id[9:]  # strip "ip-reset-" prefix

        param = next((p for p in _PARAMS if p[1] == key), None)
        if param is None:
            return
        _label, _key, _min, default, _max = param

        if self._values.get(key) == default:
            self.app.notify(f"{_label} already at default ({default})", severity="information", timeout=3)
            return

        # Update input widget + internal state, then apply.
        try:
            inp = self.query_one(f"#ip-{key}", Input)
            inp.value = str(default)
        except Exception:
            return
        self._values[key] = default

        self.run_worker(
            partial(self._apply_change, key, default, _min, _max),
            exclusive=True,
        )

    # ---- Workers ----

    async def _apply_change(
        self, key: str, val: int, min_val: int, max_val: int,
    ) -> None:
        old_val = self._values.get(key)
        self._values[key] = val

        resp = await asyncio.to_thread(
            _daemon_post, "apply_config_patch",
            {"patch": {"input_processing": {key: val}}},
        )
        if resp.get("ok"):
            self.app.notify(
                f"Input Processing: {key} = {val}",
                severity="information",
            )
            try:
                from tui.dashboard import DashboardWidget
                self.app.query_one(DashboardWidget).refresh_now()
            except Exception:
                pass
        else:
            # Revert on failure.
            self._values[key] = old_val
            try:
                inp = self.query_one(f"#ip-{key}", Input)
                inp.value = str(old_val) if old_val is not None else ""
            except Exception:
                pass
            self.app.notify(
                f"Failed to update: {resp.get('error', 'unknown')}",
                severity="error", timeout=5,
            )
