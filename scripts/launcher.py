"""Shared launcher helpers for ffchat command invocation."""

from __future__ import annotations

import shlex
import shutil
import sys
from pathlib import Path


_TERMINAL_FALLBACKS: tuple[str, ...] = ("kitty", "alacritty", "foot", "gnome-terminal")


def ffchat_argv(*args: str) -> list[str]:
    """Return the best command vector for launching the top-level `ffchat` CLI."""
    which = shutil.which("ffchat")
    if which:
        return [which, *args]
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).resolve()), *args]
    return [sys.executable, str(Path(__file__).resolve().with_name("ffchat.py")), *args]


def ffchat_tui_argv(terminal: str = "") -> list[str] | None:
    """Return a terminal command that launches `ffchat`, or None if unavailable."""
    ffchat_cmd = ffchat_argv()

    terminal_argv: list[str] = []
    if terminal.strip():
        try:
            terminal_argv = shlex.split(terminal)
        except ValueError:
            terminal_argv = []
    else:
        for name in _TERMINAL_FALLBACKS:
            which = shutil.which(name)
            if which:
                terminal_argv = [which]
                break

    if not terminal_argv:
        return None

    exe = Path(terminal_argv[0]).name
    if exe == "kitty":
        return [*terminal_argv, "--", *ffchat_cmd]
    if exe in {"alacritty", "foot"}:
        return [*terminal_argv, "-e", *ffchat_cmd]
    if exe == "gnome-terminal":
        return [*terminal_argv, "--", *ffchat_cmd]

    return [*terminal_argv, *ffchat_cmd]
