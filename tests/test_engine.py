from __future__ import annotations

import json

import pytest


def test_load_config_returns_defaults_when_file_missing(fresh_modules):
    engine = fresh_modules("engine")

    cfg = engine.load_config()

    assert cfg.flm_server.model == "gemma4-it:e4b"
    assert cfg.flm_server.power_mode == "turbo"


def test_load_config_merges_nested_sections(fresh_modules):
    engine = fresh_modules("engine")
    engine.CONFIG_PATH.write_text(
        json.dumps({
            "flm_server": {"model": "custom:model", "auto_start": False},
        }),
        encoding="utf-8",
    )

    cfg = engine.load_config()

    assert cfg.flm_server.model == "custom:model"
    assert cfg.flm_server.auto_start is False
    assert cfg.flm_server.power_mode == "turbo"


def test_save_config_writes_utf8_json_with_newline(fresh_modules):
    engine = fresh_modules("engine")
    from config import FlowkeyConfig
    cfg = FlowkeyConfig()
    cfg.theme = "hello 🙂"
    engine.save_config(cfg)

    raw = engine.CONFIG_PATH.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert json.loads(raw)["theme"] == "hello 🙂"


def test_normalize_output_collapses_whitespace():
    import llm_client

    value = llm_client.normalize_output('  Hello   world  \n  next\tline ')

    assert value == 'Hello world next line'


def test_apply_config_patch_updates_flm_model_and_runtime(fresh_modules, monkeypatch):
    engine = fresh_modules("engine")
    monkeypatch.setattr(
        engine,
        "list_flm_models",
        lambda filter_kind="installed": {"models": ["qwen3.5:4b", "other:1b"], "active": "qwen3.5:4b"},
    )
    monkeypatch.setattr(engine, "warmup_request", lambda model: None)
    monkeypatch.setattr(engine, "stop_flm_server", lambda force=True: True)
    monkeypatch.setattr(engine, "start_flm_server", lambda force_restart=False: "started")
    monkeypatch.setattr(engine, "is_flm_server_reachable", lambda: False)

    result = engine.apply_config_patch({"flm_server": {"model": "other:1b"}})

    assert result == "model=other:1b restarted"
    assert engine.FLM_MODEL == "other:1b"
    saved = json.loads(engine.CONFIG_PATH.read_text(encoding="utf-8"))
    assert saved["flm_server"]["model"] == "other:1b"


def test_apply_config_patch_rejects_uninstalled_model(fresh_modules, monkeypatch):
    engine = fresh_modules("engine")
    monkeypatch.setattr(
        engine,
        "list_flm_models",
        lambda filter_kind="installed": {"models": ["qwen3.5:4b"], "active": "qwen3.5:4b"},
    )

    with pytest.raises(RuntimeError, match="not installed"):
        engine.apply_config_patch({"flm_server": {"model": "missing:7b"}})


def test_apply_config_patch_syncs_chat_llm_model(fresh_modules, monkeypatch):
    engine = fresh_modules("engine")
    monkeypatch.setattr(
        engine,
        "list_flm_models",
        lambda filter_kind="installed": {"models": ["qwen3.5:4b", "other:1b"], "active": "qwen3.5:4b"},
    )
    monkeypatch.setattr(engine, "warmup_request", lambda model: None)
    monkeypatch.setattr(engine, "stop_flm_server", lambda force=True: True)
    monkeypatch.setattr(engine, "start_flm_server", lambda force_restart=False: "started")
    monkeypatch.setattr(engine, "is_flm_server_reachable", lambda: False)

    engine.apply_config_patch({"flm_server": {"model": "other:1b"}})

    saved = json.loads(engine.CONFIG_PATH.read_text(encoding="utf-8"))
    assert saved["flm_server"]["model"] == "other:1b"


def test_apply_config_patch_restarts_flm_server_on_model_change(fresh_modules, monkeypatch):
    engine = fresh_modules("engine")
    monkeypatch.setattr(
        engine,
        "list_flm_models",
        lambda filter_kind="installed": {"models": ["qwen3.5:4b", "other:1b"], "active": "qwen3.5:4b"},
    )
    monkeypatch.setattr(engine, "is_flm_server_reachable", lambda: False)

    call_log: list[tuple[str, object]] = []

    def fake_stop(force=True):
        call_log.append(("stop", force))

    def fake_start(force_restart=True):
        call_log.append(("start", force_restart))
        return "started"

    monkeypatch.setattr(engine, "warmup_request", lambda model: None)
    monkeypatch.setattr(engine, "stop_flm_server", fake_stop)
    monkeypatch.setattr(engine, "start_flm_server", fake_start)

    result = engine.apply_config_patch({"flm_server": {"model": "other:1b"}})

    assert result == "model=other:1b restarted"
    assert [name for name, _ in call_log] == ["stop", "start"]
    assert call_log[0][1] is True
    assert call_log[1][1] is True
    assert engine.FLM_MODEL == "other:1b"


def test_apply_config_patch_start_failure_is_non_fatal(fresh_modules, monkeypatch, caplog):
    engine = fresh_modules("engine")
    monkeypatch.setattr(
        engine,
        "list_flm_models",
        lambda filter_kind="installed": {"models": ["qwen3.5:4b", "other:1b"], "active": "qwen3.5:4b"},
    )
    monkeypatch.setattr(engine, "warmup_request", lambda model: None)
    monkeypatch.setattr(engine, "stop_flm_server", lambda force=True: True)
    monkeypatch.setattr(engine, "is_flm_server_reachable", lambda: False)

    def start_raises(**kwargs):
        raise RuntimeError("simulated start failure")

    monkeypatch.setattr(engine, "start_flm_server", start_raises)

    caplog.set_level("WARNING", logger="ffchat.engine")
    result = engine.apply_config_patch({"flm_server": {"model": "other:1b"}})

    assert result == "model=other:1b restarted"
    assert engine.FLM_MODEL == "other:1b"


def test_apply_config_patch_does_not_restart_when_model_unchanged(fresh_modules, monkeypatch):
    engine = fresh_modules("engine")
    monkeypatch.setattr(
        engine,
        "list_flm_models",
        lambda filter_kind="installed": {"models": ["qwen3.5:4b"], "active": "qwen3.5:4b"},
    )

    restart_called = {"stop": 0, "start": 0}

    def fake_stop(force=True):
        restart_called["stop"] += 1
        return True

    def fake_start(force_restart=False):
        restart_called["start"] += 1
        return "started"

    monkeypatch.setattr(engine, "stop_flm_server", fake_stop)
    monkeypatch.setattr(engine, "start_flm_server", fake_start)

    result = engine.apply_config_patch({"flm_server": {"auto_start": False}})

    assert result == "ok"
    assert restart_called == {"stop": 0, "start": 0}


def test_apply_config_patch_rejects_embedding_model(fresh_modules, monkeypatch):
    engine = fresh_modules("engine")
    engine.save_config(engine.load_config())

    def fake_list(filter_kind):
        return {"models": ["qwen3.5:4b", "embed-gemma:300m"], "active": "qwen3.5:4b"}

    monkeypatch.setattr(engine, "list_flm_models", fake_list)
    monkeypatch.setattr(engine, "warmup_request", lambda model: None)
    monkeypatch.setattr(engine, "stop_flm_server", lambda force=True: True)
    monkeypatch.setattr(engine, "start_flm_server", lambda force_restart=False: "started")

    with pytest.raises(RuntimeError, match="not a chat-selectable model"):
        engine.apply_config_patch({"flm_server": {"model": "embed-gemma:300m"}})

    on_disk = json.loads(engine.CONFIG_PATH.read_text(encoding="utf-8"))
    assert on_disk.get("flm_server", {}).get("model") != "embed-gemma:300m"


def test_apply_config_patch_rejects_asr_only_model(fresh_modules, monkeypatch):
    engine = fresh_modules("engine")
    monkeypatch.setattr(
        engine,
        "list_flm_models",
        lambda filter_kind="installed": {"models": ["qwen3.5:4b", "whisper-v3:turbo"], "active": "qwen3.5:4b"},
    )
    monkeypatch.setattr(engine, "warmup_request", lambda model: None)

    with pytest.raises(RuntimeError, match="not a chat-selectable model"):
        engine.apply_config_patch({"flm_server": {"model": "whisper-v3:turbo"}})


def test_refresh_runtime_config_falls_back_when_model_is_non_chat(
    fresh_modules, monkeypatch, caplog
):
    engine = fresh_modules("engine")
    engine.CONFIG_PATH.write_text(
        json.dumps({"flm_server": {"model": "embed-gemma:300m"}}),
        encoding="utf-8",
    )

    caplog.set_level("WARNING", logger="ffchat.engine")
    engine.refresh_runtime_config()

    assert engine.FLM_MODEL == "gemma4-it:e4b"
    assert any("embed-gemma:300m" in rec.message for rec in caplog.records)
    assert any("not a chat-selectable" in rec.message for rec in caplog.records)
