from __future__ import annotations

import json
from pathlib import Path

import config
import pytest


def test_validate_patch_file_allows_temp(tmp_path, monkeypatch):
    patch = tmp_path / "patch.json"
    patch.write_text('{"flm_model":"x"}', encoding="utf-8")
    assert config.validate_patch_file(patch) == patch.resolve()


def test_validate_patch_file_rejects_outside_allowed(tmp_path):
    outside = Path("C:/Windows/System32/drivers/etc/hosts")
    if not outside.exists():
        pytest.skip("hosts file not present")
    with pytest.raises(ValueError, match="outside allowed"):
        config.validate_patch_file(outside)


def test_filter_config_patch_modes_whitelist():
    patch = {
        "transform_hotkeys": {
            "grammar": "Ctrl+Alt+G",
            "prompt": "Ctrl+Shift+P",
            "evil": "blocked",
        },
        "interaction_hotkeys": {
            "open_chat": "ctrl+alt+t",
            "capture_note": "ctrl+alt+n",
            "ask_chat": "ctrl+alt+a",
            "grammar_fix": "blocked",
        },
        "modes": {
            "tone": {"preset": "casual"},
            "grammar": {"system_prompt": "evil"},
        }
    }
    filtered = config.filter_config_patch(patch)
    assert filtered == {
        "transform_hotkeys": {"grammar": "Ctrl+Alt+G", "prompt": "Ctrl+Shift+P"},
        "interaction_hotkeys": {
            "open_chat": "ctrl+alt+t",
            "capture_note": "ctrl+alt+n",
            "ask_chat": "ctrl+alt+a",
        },
        "modes": {"tone": {"preset": "casual"}},
    }


def test_save_config_atomic(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg = config.FlowkeyConfig()
    cfg.flm_server.model = "a"
    config.save_config(cfg_path, cfg)
    cfg2 = config.FlowkeyConfig()
    cfg2.flm_server.model = "b"
    config.save_config(cfg_path, cfg2)
    loaded = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert loaded["flm_server"]["model"] == "b"


def test_load_config_deep_merges_mode_defaults(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "transform_hotkeys": {
            "grammar": "Ctrl+Alt+G",
            "prompt": "Ctrl+Shift+P",
        },
        "modes": {
            "tone": {"preset": "casual"},
            "summarize": {"label": "Summarize"},
        }
    }), encoding="utf-8")

    loaded = config.load_config(cfg_path)

    assert loaded.transform_hotkeys.grammar == "Ctrl+Alt+G"
    assert loaded.transform_hotkeys.prompt == "Ctrl+Shift+P"
    assert loaded.modes["tone"].preset == "casual"
    assert loaded.modes["tone"].presets is not None
    # summarize mode provided via config is loaded with defaults for unspecified fields
    assert loaded.modes["summarize"].label == "Summarize"
