"""Subprocess wrappers with safe defaults.

Thin wrappers over subprocess.run / subprocess.Popen that default to
capture_output=True, text=True, check=False and scrub bundled-runtime
library paths when launching external `flm` processes.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path


def _bundle_internal_dir() -> Path | None:
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve().parent / "_internal"


def _flm_child_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return an env mapping safe for invoking the system `flm` CLI."""
    child_env = dict(os.environ if env is None else env)
    bundle_dir = _bundle_internal_dir()
    if bundle_dir is not None:
        bundle_text = str(bundle_dir)
        ld_path = child_env.get("LD_LIBRARY_PATH")
        if ld_path:
            parts = [part for part in ld_path.split(":") if part and part != bundle_text]
            if parts:
                child_env["LD_LIBRARY_PATH"] = ":".join(parts)
            else:
                child_env.pop("LD_LIBRARY_PATH", None)
    return child_env


def run_captured(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    kwargs.setdefault("check", False)
    return subprocess.run(argv, **kwargs)


def run_flm(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run `flm` with Flowkey's bundle-specific library path removed."""
    kwargs["env"] = _flm_child_env(kwargs.get("env"))
    return subprocess.run(argv, **kwargs)


def popen_flm(argv: list[str], **kwargs) -> subprocess.Popen:
    """Start `flm` with Flowkey's bundle-specific library path removed."""
    kwargs["env"] = _flm_child_env(kwargs.get("env"))
    return subprocess.Popen(argv, **kwargs)
