"""Subprocess wrapper with safe defaults.

Thin wrapper over subprocess.run that defaults to
capture_output=True, text=True, check=False.
"""

from __future__ import annotations

import subprocess


def run_captured(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    kwargs.setdefault("check", False)
    return subprocess.run(argv, **kwargs)
