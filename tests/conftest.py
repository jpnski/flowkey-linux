from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SCRIPTS_DIR = REPO_ROOT / "scripts"

if str(REPO_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_SCRIPTS_DIR))


@pytest.fixture
def isolated_checkout_root(tmp_path: Path) -> Path:
    root = tmp_path / "release_root"
    scripts = root / "scripts"
    shutil.copytree(
        REPO_SCRIPTS_DIR,
        scripts,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def fresh_modules(isolated_checkout_root: Path):
    scripts_dir = isolated_checkout_root / "scripts"
    sys.path.insert(0, str(scripts_dir))
    for name in [
        "paths",
        "engine",
        "daemon",
        "notes",
        "install",
        "settings_gui",
        "pull",
    ]:
        sys.modules.pop(name, None)

    modules = {}

    def _load(*names: str):
        loaded = []
        for name in names:
            module = importlib.import_module(name)
            modules[name] = module
            loaded.append(module)
        return loaded[0] if len(loaded) == 1 else tuple(loaded)

    yield _load

    for name in list(modules):
        sys.modules.pop(name, None)
    try:
        sys.path.remove(str(scripts_dir))
    except ValueError:
        pass


@pytest.fixture
def sample_history():
    return [
        {
            "timestamp": "2026-05-27T09:00:00",
            "mode": "grammar",
            "elapsed_seconds": 0.5,
            "tok_per_sec": 18.0,
            "prompt_tokens": 11,
            "completion_tokens": 9,
            "input_chars": 24,
        },
        {
            "timestamp": "2026-05-27T10:15:00",
            "mode": "prompt",
            "elapsed_seconds": 1.2,
            "tok_per_sec": 12.5,
            "prompt_tokens": 25,
            "completion_tokens": 15,
            "input_chars": 120,
        },
        {
            "timestamp": "2026-05-27T10:45:00",
            "mode": "grammar",
            "elapsed_seconds": 2.0,
            "tok_per_sec": 10.0,
            "prompt_tokens": 30,
            "completion_tokens": 20,
            "input_chars": 160,
        },
    ]
