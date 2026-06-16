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


def test_filter_config_patch_whitelist():
    patch = {
        "flm_server": {"model": "gemma3:4b", "power_mode": "performance"},
        "chat": {"temperature": 0.5},
        "evil": "blocked",
    }
    filtered = config.filter_config_patch(patch)
    allowed = {
        "flm_server": {"model": "gemma3:4b", "power_mode": "performance"},
        "chat": {"temperature": 0.5},
    }
    assert filtered == allowed
    assert "evil" not in filtered


def test_filter_config_patch_rejects_non_whitelisted_sections():
    patch = {"terminal": "kitty", "modes": {}, "notes": {}}
    filtered = config.filter_config_patch(patch)
    assert filtered == {}


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


def test_load_config_deep_merges_sections(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "slash_commands": [
            {"name": "custom", "system_prompt": "Do X", "description": "", "max_tokens": 512},
        ],
    }), encoding="utf-8")

    loaded = config.load_config(cfg_path)

    assert len(loaded.slash_commands) == 1
    assert loaded.slash_commands[0].name == "custom"
    assert loaded.slash_commands[0].system_prompt == "Do X"
    # Other fields get defaults
    assert loaded.flm_server.model == "gemma4-it:e4b"


def test_flowkey_config_defaults():
    cfg = config.FlowkeyConfig()
    assert cfg.flm_server.model == "gemma4-it:e4b"
    assert cfg.flm_server.power_mode == "balanced"
    assert cfg.slash_commands == []
