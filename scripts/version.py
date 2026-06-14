"""Single source of truth for the Flowkey package version."""

from __future__ import annotations

try:
    from importlib.metadata import version as _pkg_version

    APP_VERSION = _pkg_version("flowkey")
except Exception:
    APP_VERSION = "0.0.0"
