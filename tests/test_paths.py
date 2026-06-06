"""Tests for paths.py (Linux-only)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path


def _reload_paths():
    sys.modules.pop("paths", None)
    return importlib.import_module("paths")


def test_paths_prefers_env_override(monkeypatch, tmp_path: Path):
    custom = tmp_path / "custom-root"
    monkeypatch.setenv("FFP_RELEASE_ROOT", str(custom))

    paths = _reload_paths()

    assert paths.RELEASE_ROOT == custom.resolve()
    assert paths.CONFIG_FILE == custom / "config.json"


def test_paths_uses_xdg_data_home_when_not_release_layout(monkeypatch):
    monkeypatch.delenv("FFP_RELEASE_ROOT", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", "/home/user/.local/share")

    paths = _reload_paths()
    monkeypatch.setattr(paths, "_looks_like_dev_root", lambda path: False)
    monkeypatch.setattr(paths, "_is_under_prefix", lambda path: False)

    # user-local mode collapses APP_DIR onto XDG_DATA_HOME root.
    assert paths._user_local_root() == Path("/home/user/.local/share/Flowkey")


def test_user_local_root_falls_back_to_home_dot_local(monkeypatch):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", "/home/testuser")

    paths = _reload_paths()
    monkeypatch.setattr(paths, "_looks_like_dev_root", lambda path: False)
    monkeypatch.setattr(paths, "_is_under_prefix", lambda path: False)

    assert paths._user_local_root() == Path("/home/testuser/.local/share/Flowkey")


def test_ensure_dirs_creates_runtime_folders(monkeypatch, tmp_path: Path):
    root = tmp_path / "runtime-root"
    monkeypatch.setenv("FFP_RELEASE_ROOT", str(root))
    paths = _reload_paths()

    paths.ensure_dirs()

    assert paths.CONFIG_FILE.parent.exists()
    assert paths.DATA_DIR.exists()
    assert paths.LOGS_DIR.exists()


def test_migrate_legacy_layout_moves_known_runtime_files(monkeypatch, tmp_path: Path):
    root = tmp_path / "release-root"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    monkeypatch.setenv("FFP_RELEASE_ROOT", str(root))
    paths = _reload_paths()

    legacy = paths.legacy_scripts_path("prompt_history.jsonl")
    legacy.write_text("{}", encoding="utf-8")
    moved = paths.migrate_legacy_layout()

    assert any("prompt_history.jsonl" in line for line in moved)
    assert paths.PROMPT_HISTORY_FILE.exists()



