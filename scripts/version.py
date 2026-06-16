"""Single source of truth for the ffchat package version."""

from __future__ import annotations

try:
    from importlib.metadata import version as _pkg_version

    APP_VERSION = _pkg_version("ffchat")
except Exception:
    APP_VERSION = "0.1.0"
