r"""Centralized path resolver for Flowkey (Linux-only).

All other modules import path constants from here instead of constructing
their own `Path(__file__).resolve().parent / "foo.json"` lines. That way
moving files between folders (config/, data/, logs/, setup/) only requires
editing this file.

Two runtime modes are recognized:

1. **dev** — running from a source checkout or any non-frozen Python launch.

       ./
        ├── scripts/   ← Python source (this file lives here)
        │   └── config.seed.json   ← tracked seed config
        ├── config/
        ├── data/      ← runtime data
        └── logs/      ← daemon.log, flm_server.log

   APP_DIR = USER_ROOT = the app root (single tree).

2. **deployed** — running as a frozen binary.

   APP_DIR is still the application/bundle root, but user-mutable state lives
   under XDG_DATA_HOME so the bundle directory stays read-only.

Anything that is not frozen behaves like dev. There is no separate installed-
Python runtime mode.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ---------- Mode detection ---------------------------------------------------

SCRIPTS_DIR: Path = Path(__file__).resolve().parent


def _user_local_root() -> Path:
    """User-local writable root for runtime state.

    Uses $XDG_DATA_HOME/Flowkey, falling back to ~/.local/share/Flowkey.
    """
    xdg_data = os.environ.get("XDG_DATA_HOME")
    if xdg_data:
        return (Path(xdg_data) / "Flowkey").resolve()
    home = os.path.expanduser("~")
    if home:
        return (Path(home) / ".local" / "share" / "Flowkey").resolve()
    return SCRIPTS_DIR.parent  # last-ditch fallback


INSTALL_MODE: str = "deployed" if getattr(sys, "frozen", False) else "dev"


# ---------- Directories ------------------------------------------------------

def _resolve_app_dir() -> Path:
    return SCRIPTS_DIR.parent


def _resolve_user_root() -> Path:
    """Where user-mutable state (config/data/logs) lives."""
    if INSTALL_MODE == "deployed":
        return _user_local_root()
    return APP_DIR


APP_DIR: Path = _resolve_app_dir()
USER_ROOT: Path = _resolve_user_root()

DATA_DIR:   Path = USER_ROOT / "data"
LOGS_DIR:   Path = USER_ROOT / "logs"


def ensure_dirs() -> None:
    """Create writable runtime folders on demand. Cheap to call repeatedly."""
    for d in (DATA_DIR, LOGS_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ---------- Named files ------------------------------------------------------

# Config — flattened at USER_ROOT (no subdirectory)
CONFIG_FILE:         Path = USER_ROOT / "config.json"

# Runtime data
COUNTERS_FILE:       Path = DATA_DIR / "prompt_counters.ini"
PROMPT_HISTORY_FILE: Path = DATA_DIR / "prompt_history.jsonl"
GRAMMAR_HISTORY_FILE: Path = DATA_DIR / "grammar_fix_history.jsonl"
CHAT_THREADS_FILE:   Path = DATA_DIR / "chat_threads.jsonl"
FLM_PID_FILE:        Path = DATA_DIR / "flm_server.pid"

# Markers (tiny presence-only files)
MARKER_CLIPBOARD_WATCHER: Path = DATA_DIR / ".clipboard_watcher_on"
MARKER_FIRST_RUN_DONE:    Path = DATA_DIR / ".first_run_done"
MARKER_OPEN_DASHBOARD:    Path = DATA_DIR / ".open_dashboard"

# Logs
DAEMON_LOG_FILE:     Path = LOGS_DIR / "daemon.log"
FLM_SERVER_LOG_FILE: Path = LOGS_DIR / "flm_server.log"

# Source template shipped in the source tree. On first run we copy it to
# CONFIG_FILE if the user doesn't already have one.
CONFIG_SEED_FILE:    Path = SCRIPTS_DIR / "config.seed.json"


def seed_config_if_missing() -> bool:
    """Copy the bundled default config into the user's CONFIG_DIR on first run.

    Returns True if a copy happened. Idempotent: no-op if CONFIG_FILE already
    exists or if the seed is missing (dev tree may not ship one).
    """
    if CONFIG_FILE.exists():
        return False
    if not CONFIG_SEED_FILE.exists():
        return False
    ensure_dirs()
    CONFIG_FILE.write_bytes(CONFIG_SEED_FILE.read_bytes())
    return True
