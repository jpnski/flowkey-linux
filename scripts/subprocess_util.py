"""Shared subprocess helpers for Linux background execution.

Simple wrappers over stdlib subprocess with opinionated defaults
(capture_output=True, text=True, check=False). No Windows-specific flags.
"""

from __future__ import annotations

import subprocess

NO_WINDOW: int = 0  # kept for back-compat with callers that still reference it


def run_hidden(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    kwargs.setdefault("check", False)
    return subprocess.run(argv, **kwargs)


def popen_hidden(argv: list[str], **kwargs) -> subprocess.Popen:
    return subprocess.Popen(argv, **kwargs)
