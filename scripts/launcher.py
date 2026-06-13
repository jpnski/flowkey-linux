"""Shared launcher helpers for Flowkey command invocation."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def flowkey_argv(*args: str) -> list[str]:
    """Return the best command vector for launching the top-level `flowkey` CLI."""
    which = shutil.which("flowkey")
    if which:
        return [which, *args]
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).resolve()), *args]
    return [sys.executable, str(Path(__file__).resolve().with_name("flowkey.py")), *args]
