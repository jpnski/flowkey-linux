from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


def test_load_config_returns_defaults_when_file_missing(fresh_modules):
    engine = fresh_modules("engine")

    cfg = engine.load_config()

    assert cfg.flm_server.model == "gemma4-it:e4b"
    assert cfg.flm_server.power_mode == "turbo"
    assert cfg.history.store_text is False


def test_load_config_merges_nested_sections(fresh_modules):
    engine = fresh_modules("engine")
    engine.CONFIG_PATH.write_text(
        json.dumps(
                {
                    "flm_server": {"model": "custom:model", "auto_start": False},
                    "input_processing": {"chunk_size": 900},
                    "grammar_ignore_words": ["Flowkey"],
                    "transform_hotkeys": {
                        "grammar": "Ctrl+Alt+G",
                        "prompt": "Ctrl+Shift+P",
                    },
                    "modes": {
                        "grammar": {"label": "Grammar fix", "description": "", "system_prompt": "", "max_tokens": {"short": 160, "medium": 220, "long": 280}},
                        "prompt": {"label": "Prompt fix", "description": "", "system_prompt": "test prompt", "max_tokens": {"short": 300, "medium": 500, "long": 700}},
                    },
                }
            ),
        encoding="utf-8",
    )

    cfg = engine.load_config()

    assert cfg.flm_server.model == "custom:model"
    assert cfg.flm_server.auto_start is False
    assert cfg.flm_server.power_mode == "turbo"
    assert cfg.input_processing.chunk_size == 900
    assert cfg.grammar_ignore_words == ["Flowkey"]
    assert cfg.transform_hotkeys.grammar == "Ctrl+Alt+G"
    assert cfg.transform_hotkeys.prompt == "Ctrl+Shift+P"
    assert cfg.modes["grammar"].label == "Grammar fix"
    assert cfg.modes["prompt"].system_prompt == "test prompt"


def test_save_config_writes_utf8_json_with_newline(fresh_modules):
    engine = fresh_modules("engine")
    from config import FlowkeyConfig
    cfg = FlowkeyConfig()
    cfg.theme = "hello 🙂"
    engine.save_config(cfg)

    raw = engine.CONFIG_PATH.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert json.loads(raw)["theme"] == "hello 🙂"


def test_list_hotkeys_prints_human_readable_shortcuts(fresh_modules, capsys):
    engine = fresh_modules("engine")

    engine.list_hotkeys()

    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("mode")
    assert any(line.startswith("grammar") and "ctrl+alt+g" in line for line in out)
    assert any(line.startswith("prompt") and "ctrl+alt+p" in line for line in out)
    assert all("\t" not in line for line in out)


def test_normalize_output_cleans_smart_punctuation_and_spacing(fresh_modules):
    engine = fresh_modules("engine")

    value = engine.normalize_output(' “Hello” — world  \n  next\tline ')

    assert value == '"Hello" - world\nnext line'


def test_append_history_writes_jsonl_line(fresh_modules):
    engine = fresh_modules("engine")
    entry = {"mode": "grammar", "elapsed_seconds": 0.2}

    engine.append_history(entry)

    rows = engine.HISTORY_PATH.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    assert json.loads(rows[0]) == entry


def test_split_chunks_returns_single_chunk_when_short(fresh_modules):
    engine = fresh_modules("engine")

    assert engine._split_chunks("short text", 50) == ["short text"]


def test_split_chunks_prefers_newline_boundaries(fresh_modules):
    engine = fresh_modules("engine")
    engine.INPUT_PROCESSING_CFG.min_chunk_size = 20
    text = ("alpha " * 20).strip() + "\n" + ("beta " * 20).strip()

    chunks = engine._split_chunks(text, 120)

    assert len(chunks) == 2
    assert chunks[0].endswith("alpha")
    assert chunks[1].startswith("beta")


def test_split_chunks_merges_tiny_trailing_chunk(fresh_modules):
    engine = fresh_modules("engine")
    engine.INPUT_PROCESSING_CFG.min_chunk_size = 50
    text = ("alpha " * 30) + "tail"

    chunks = engine._split_chunks(text, 80)

    assert len(chunks) == 2
    assert chunks[-1].endswith("tail")


@pytest.mark.parametrize(
    ("mode", "text", "strategy"),
    [
        ("prompt", "tiny", "prompt_short"),
        ("prompt", "x" * 600, "prompt_medium"),
        ("grammar", "x" * 800, "grammar_medium"),
        ("grammar", "x" * 1500, "grammar_long"),
    ],
)
def test_resolve_token_budget_picks_expected_strategy(fresh_modules, mode, text, strategy):
    engine = fresh_modules("engine")

    _, selected = engine._resolve_token_budget(mode, text)

    assert selected == strategy


def test_dict_protect_returns_original_when_no_words(fresh_modules, monkeypatch):
    engine = fresh_modules("engine")
    engine.GRAMMAR_IGNORE_WORDS = []
    engine.GRAMMAR_IGNORE_WORDS = ["Flowkey", "LLM"]
    engine.GRAMMAR_IGNORE_WORDS = ["fastflowprompt"]
    engine.GRAMMAR_IGNORE_WORDS = ["Flowkey"]
    monkeypatch.setattr(engine, "is_flm_server_reachable", lambda: True)
    calls = []

    def fake_call(model, system_prompt, user_content, max_tokens, timeout_seconds):
        calls.append((model, system_prompt, user_content, max_tokens, timeout_seconds))
        return ("__FFPDICT0__ fixed", model)

    monkeypatch.setattr(engine, "_call_flm_api", fake_call)

    text, elapsed, model, strategy = engine.call_flm("grammar", "Flowkey ok")

    assert text == "Flowkey fixed"
    assert elapsed >= 0
    assert model == engine.FLM_MODEL
    assert strategy == "grammar_short"
    assert "Fix grammar and punctuation only." in calls[0][1]


def test_call_flm_prompt_retries_on_near_verbatim_output(fresh_modules, monkeypatch):
    engine = fresh_modules("engine")
    monkeypatch.setattr(engine, "is_flm_server_reachable", lambda: True)
    responses = iter(
        [
            ("Task: Build a plan", engine.FLM_MODEL),
            ("Create a concrete execution plan with deliverables.", engine.FLM_MODEL),
        ]
    )
    prompts = []

    def fake_call(model, system_prompt, user_content, max_tokens, timeout_seconds):
        prompts.append(system_prompt)
        return next(responses)

    monkeypatch.setattr(engine, "_call_flm_api", fake_call)

    text, _, _, strategy = engine.call_flm("prompt", "Task: Build a plan")

    assert strategy == "prompt_short"
    assert text == "Create a concrete execution plan with deliverables."
    assert any("meta-framing" in prompt for prompt in prompts)


def test_prompt_mode_cli_writes_output_file(fresh_modules, monkeypatch, tmp_path: Path):
    engine = fresh_modules("engine")
    monkeypatch.setattr(engine, "is_flm_server_reachable", lambda: True)

    def fake_call(model, system_prompt, user_content, max_tokens, timeout_seconds):
        assert "Claude-ready" in system_prompt
        return ("<task>Refine onboarding email</task>", model)

    monkeypatch.setattr(engine, "_call_flm_api", fake_call)

    in_path = tmp_path / "in.txt"
    out_path = tmp_path / "out.txt"
    in_path.write_text("refine onboarding email", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "engine.py",
            "--mode",
            "prompt",
            "--input-file",
            str(in_path),
            "--output-file",
            str(out_path),
        ],
    )
    engine.main()
    assert "<task>" in out_path.read_text(encoding="utf-8")


def test_process_cli_stops_flm_server_after_one_shot_auto_start(fresh_modules, monkeypatch, tmp_path: Path):
    engine = fresh_modules("engine")
    reachability = [False, True]
    stopped = []

    monkeypatch.setattr(engine, "is_flm_server_reachable", lambda: reachability.pop(0))
    monkeypatch.setattr(engine, "call_flm", lambda mode, text: ("done", 0.5, engine.FLM_MODEL, "grammar_short"))
    monkeypatch.setattr(engine, "stop_flm_server", lambda force=True: stopped.append(force) or True)

    in_path = tmp_path / "in.txt"
    out_path = tmp_path / "out.txt"
    in_path.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "engine.py",
            "--mode",
            "grammar",
            "--input-file",
            str(in_path),
            "--output-file",
            str(out_path),
        ],
    )

    engine.main()

    assert stopped == [True]
    assert out_path.read_text(encoding="utf-8") == "done"


def test_process_cli_keep_flm_server_skips_stop(fresh_modules, monkeypatch, tmp_path: Path):
    engine = fresh_modules("engine")
    monkeypatch.setattr(engine, "is_flm_server_reachable", lambda: False)
    monkeypatch.setattr(engine, "call_flm", lambda mode, text: ("done", 0.5, engine.FLM_MODEL, "grammar_short"))
    stopped = []
    monkeypatch.setattr(engine, "stop_flm_server", lambda force=True: stopped.append(force) or True)

    in_path = tmp_path / "in.txt"
    out_path = tmp_path / "out.txt"
    in_path.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "engine.py",
            "--mode",
            "grammar",
            "--keep-flm-server",
            "--input-file",
            str(in_path),
            "--output-file",
            str(out_path),
        ],
    )

    engine.main()

    assert stopped == []


def test_call_flm_prompt_rejects_prompt_colon_echo(fresh_modules, monkeypatch):
    engine = fresh_modules("engine")
    monkeypatch.setattr(engine, "is_flm_server_reachable", lambda: True)
    echo = "Prompt: Develop an app for Java that plays a game of ducks."

    def fake_call(model, system_prompt, user_content, max_tokens, timeout_seconds):
        return (echo, engine.FLM_MODEL)

    monkeypatch.setattr(engine, "_call_flm_api", fake_call)

    text, _, _, _ = engine.call_flm("prompt", "Develop a app for java that play a game of ducks")

    assert text.startswith("<task>")
    assert not text.lower().startswith("prompt:")


def test_call_flm_prompt_falls_back_to_force_shape_when_rescue_fails(fresh_modules, monkeypatch):
    engine = fresh_modules("engine")
    monkeypatch.setattr(engine, "is_flm_server_reachable", lambda: True)

    def fake_call(model, system_prompt, user_content, max_tokens, timeout_seconds):
        if "Claude-ready prompt for Anthropic" in system_prompt:
            raise RuntimeError("rescue failed")
        return ("just copy me", model)

    monkeypatch.setattr(engine, "_call_flm_api", fake_call)

    text, _, _, _ = engine.call_flm("prompt", "just copy me")

    assert text.startswith("<task>")


def test_apply_config_patch_updates_flm_model_and_runtime(fresh_modules, monkeypatch):
    engine = fresh_modules("engine")
    monkeypatch.setattr(
        engine,
        "list_flm_models",
        lambda: {"models": ["qwen3.5:4b", "other:1b"], "active": "qwen3.5:4b"},
    )
    monkeypatch.setattr(engine, "warmup_request", lambda model: None)
    monkeypatch.setattr(engine, "stop_flm_server", lambda force=True: True)
    monkeypatch.setattr(engine, "start_flm_server", lambda force_restart=False: "started")

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
        lambda: {"models": ["qwen3.5:4b"], "active": "qwen3.5:4b"},
    )

    with pytest.raises(RuntimeError, match="not installed"):
        engine.apply_config_patch({"flm_server": {"model": "missing:7b"}})


def test_apply_config_patch_syncs_chat_llm_model(fresh_modules, monkeypatch):
    engine = fresh_modules("engine")
    monkeypatch.setattr(
        engine,
        "list_flm_models",
        lambda: {"models": ["qwen3.5:4b", "other:1b"], "active": "qwen3.5:4b"},
    )
    monkeypatch.setattr(engine, "warmup_request", lambda model: None)
    monkeypatch.setattr(engine, "stop_flm_server", lambda force=True: True)
    monkeypatch.setattr(engine, "start_flm_server", lambda force_restart=False: "started")

    engine.apply_config_patch({"flm_server": {"model": "other:1b"}})

    saved = json.loads(engine.CONFIG_PATH.read_text(encoding="utf-8"))
    assert saved["flm_server"]["model"] == "other:1b"


def test_apply_config_patch_restarts_flm_server_on_model_change(fresh_modules, monkeypatch):
    engine = fresh_modules("engine")
    monkeypatch.setattr(
        engine,
        "list_flm_models",
        lambda: {"models": ["qwen3.5:4b", "other:1b"], "active": "qwen3.5:4b"},
    )

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
        lambda: {"models": ["qwen3.5:4b", "other:1b"], "active": "qwen3.5:4b"},
    )
    monkeypatch.setattr(engine, "warmup_request", lambda model: None)
    monkeypatch.setattr(engine, "stop_flm_server", lambda force=True: True)

    def start_raises(**kwargs):
        raise RuntimeError("simulated start failure")

    monkeypatch.setattr(engine, "start_flm_server", start_raises)

    caplog.set_level("WARNING", logger="flowkey.engine")
    result = engine.apply_config_patch({"flm_server": {"model": "other:1b"}})

    assert result == "model=other:1b restarted"
    assert engine.FLM_MODEL == "other:1b"


def test_apply_config_patch_does_not_restart_when_model_unchanged(fresh_modules, monkeypatch):
    engine = fresh_modules("engine")
    monkeypatch.setattr(
        engine,
        "list_flm_models",
        lambda: {"models": ["qwen3.5:4b"], "active": "qwen3.5:4b"},
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
    # Seed the config file so load_config succeeds and merge + validate runs.
    engine.save_config(engine.load_config())
    list_calls = {"n": 0}

    def fake_list():
        list_calls["n"] += 1
        # Simulate the embedding model being installed — the new chat-selectable
        # check must still reject it before this list is consulted.
        return {"models": ["qwen3.5:4b", "embed-gemma:300m"], "active": "qwen3.5:4b"}

    monkeypatch.setattr(engine, "list_flm_models", fake_list)
    monkeypatch.setattr(engine, "warmup_request", lambda model: None)
    monkeypatch.setattr(engine, "stop_flm_server", lambda force=True: True)
    monkeypatch.setattr(engine, "start_flm_server", lambda force_restart=False: "started")

    with pytest.raises(RuntimeError, match="not a chat-selectable model"):
        engine.apply_config_patch({"flm_server": {"model": "embed-gemma:300m"}})

    # The file on disk is untouched (no half-write).
    on_disk = json.loads(engine.CONFIG_PATH.read_text(encoding="utf-8"))
    assert on_disk.get("flm_server", {}).get("model") != "embed-gemma:300m"


def test_apply_config_patch_rejects_asr_only_model(fresh_modules, monkeypatch):
    engine = fresh_modules("engine")
    monkeypatch.setattr(
        engine,
        "list_flm_models",
        lambda: {"models": ["qwen3.5:4b", "whisper-v3:turbo"], "active": "qwen3.5:4b"},
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

    caplog.set_level("WARNING", logger="flowkey.engine")
    engine.refresh_runtime_config()

    assert engine.FLM_MODEL == "gemma4-it:e4b"
    assert any("embed-gemma:300m" in rec.message for rec in caplog.records)
    assert any("not a chat-selectable" in rec.message for rec in caplog.records)


def test_read_and_write_output_file_modes(fresh_modules, monkeypatch, tmp_path: Path):
    engine = fresh_modules("engine")
    input_path = tmp_path / "input.txt"
    output_path = tmp_path / "output.txt"
    input_path.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(
        engine.sys,
        "argv",
        ["engine.py", "--input-file", str(input_path), "--output-file", str(output_path)],
    )

    assert engine._read_input_text() == "hello"
    engine._write_output_text("world")
    assert output_path.read_text(encoding="utf-8") == "world"
