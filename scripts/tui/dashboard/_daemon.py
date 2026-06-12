from __future__ import annotations

import logging
from typing import Any

import loopback_http

log = logging.getLogger("flowkey.tui.dashboard")

DAEMON_BASE_URL = loopback_http.DAEMON_BASE_URL
REFRESH_INTERVAL = 10.0  # seconds between auto-refresh

# ---------------------------------------------------------------------------
# Data fetcher helpers
# ---------------------------------------------------------------------------

_DAEMON_TIMEOUT_DEFAULT = 5.0
_DAEMON_TIMEOUT_MODEL_CHANGE = 75.0  # Must accommodate: stop_flm (~3s) + start_flm port-poll (up to 25s) + warmup request (up to 30s) + buffer
_DAEMON_TIMEOUT_PULL_START = 25.0
_DAEMON_TIMEOUT_PULL_CANCEL = 10.0

# Re-export for backward compatibility — all dashboard panes import from here.
_daemon_post = loopback_http.daemon_post


def _resolve_result(resp: dict) -> Any:
    """Extract result from daemon response, or error string."""
    if resp.get("ok"):
        return resp.get("result")
    return f"Error: {resp.get('error', 'unknown')}"
