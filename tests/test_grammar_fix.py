from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


def test_load_config_returns_defaults_when_file_missing(fresh_modules):
    grammar_fix = fresh_modules("grammar_fix")

    cfg = grammar_fix.load_config()

    assert cfg["flm_config"]["active_model"] == "gemma4-it:e4b"
    assert cfg["flm_config"]["power_mode"] == "balanced"
    assert cfg["history_config"]["store_text"] is False


def test_load_config_merges_nested_sections(fresh_modules):
    grammar_fix = fresh_modules("grammar_fix")
    grammar_fix.CONFIG_PATH.write_text(
        json.dumps(
            {
                "flm_config": {"active_model": "custom:model"},
                "flm_serving_config": {"auto_start": False},
                "input_processing": {"chunk_size": 900},
                "grammar_ignore_words": ["Flowkey"],
                "modes": {"grammar": {"shortcut": "Ctrl+Alt+G"}},
            }
        ),
        encoding="utf-8",
    )

    cfg = grammar_fix.load_config()

    assert cfg["flm_config"]["active_model"] == "custom:model"
    assert cfg["flm_serving_config"]["auto_start"] is False
    assert cfg["flm_config"]["power_mode"] == "balanced"
    assert cfg["input_processing"]["chunk_size"] == 900
    assert cfg["grammar_ignore_words"] == ["Flowkey"]
    assert cfg["modes"]["grammar"]["shortcut"] == "Ctrl+Alt+G"
    assert "prompt" in cfg["modes"]


def test_save_config_writes_utf8_json_with_newline(fresh_modules):
    grammar_fix = fresh_modules("grammar_fix")
    payload = {"message": "hello 🙂", "server": {"auto_start": True}}

    grammar_fix.save_config(payload)

    raw = grammar_fix.CONFIG_PATH.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert json.loads(raw)["message"] == "hello 🙂"


@pytest.mark.parametrize(
    ("shortcut", "expected"),
    [
        ("Ctrl+Shift+G", "^+g"),
        ("Alt+N", "!n"),
        ("Win+T", "#t"),
        ("", ""),
        ("Ctrl + Shift + A", "^+a"),
    ],
)
def test_shortcut_to_compact_translates_variants(fresh_modules, shortcut, expected):
    grammar_fix = fresh_modules("grammar_fix")

    assert grammar_fix.shortcut_to_compact(shortcut) == expected


def test_normalize_output_cleans_smart_punctuation_and_spacing(fresh_modules):
    grammar_fix = fresh_modules("grammar_fix")

    value = grammar_fix.normalize_output(' “Hello” — world  \n  next\tline ')

    assert value == '"Hello" - world\nnext line'


def test_append_history_writes_jsonl_line(fresh_modules):
    grammar_fix = fresh_modules("grammar_fix")
    entry = {"mode": "grammar", "elapsed_seconds": 0.2}

    grammar_fix.append_history(entry)

    rows = grammar_fix.HISTORY_PATH.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    assert json.loads(rows[0]) == entry


def test_split_chunks_returns_single_chunk_when_short(fresh_modules):
    grammar_fix = fresh_modules("grammar_fix")

    assert grammar_fix._split_chunks("short text", 50) == ["short text"]


def test_split_chunks_prefers_newline_boundaries(fresh_modules):
    grammar_fix = fresh_modules("grammar_fix")
    grammar_fix.INPUT_PROCESSING_CFG["min_chunk_size"] = 20
    text = ("alpha " * 20).strip() + "\n" + ("beta " * 20).strip()

    chunks = grammar_fix._split_chunks(text, 120)

    assert len(chunks) == 2
    assert chunks[0].endswith("alpha")
    assert chunks[1].startswith("beta")


def test_split_chunks_merges_tiny_trailing_chunk(fresh_modules):
    grammar_fix = fresh_modules("grammar_fix")
    grammar_fix.INPUT_PROCESSING_CFG["min_chunk_size"] = 50
    text = ("alpha " * 30) + "tail"

    chunks = grammar_fix._split_chunks(text, 80)

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
    grammar_fix = fresh_modules("grammar_fix")

    _, selected = grammar_fix._resolve_token_budget(mode, text)

    assert selected == strategy


def test_dict_protect_returns_original_when_no_words(fresh_modules):
    grammar_fix = fresh_modules("grammar_fix")
    grammar_fix.GRAMMAR_IGNORE_WORDS = []
    grammar_fix.GRAMMAR_IGNORE_WORDS = ["Flowkey", "LLM"]
    grammar_fix.GRAMMAR_IGNORE_WORDS = ["fastflowprompt"]
    grammar_fix.GRAMMAR_IGNORE_WORDS = ["Flowkey"]
    monkeypatch.setattr(grammar_fix, "is_flm_server_reachable", lambda: True)
    calls = []

    def fake_call(model, system_prompt, user_content, max_tokens, timeout_seconds):
        calls.append((model, system_prompt, user_content, max_tokens, timeout_seconds))
        return ("__FFPDICT0__ fixed", model)

    monkeypatch.setattr(grammar_fix, "_call_flm_api", fake_call)

    text, elapsed, model, strategy = grammar_fix.call_flm("grammar", "Flowkey ok")

    assert text == "Flowkey fixed"
    assert elapsed >= 0
    assert model == grammar_fix.FLM_MODEL
    assert strategy == "grammar_short"
    assert "Fix grammar and punctuation only." in calls[0][1]


def test_call_flm_prompt_retries_on_near_verbatim_output(fresh_modules, monkeypatch):
    grammar_fix = fresh_modules("grammar_fix")
    monkeypatch.setattr(grammar_fix, "is_flm_server_reachable", lambda: True)
    responses = iter(
        [
            ("Task: Build a plan", grammar_fix.FLM_MODEL),
            ("Create a concrete execution plan with deliverables.", grammar_fix.FLM_MODEL),
        ]
    )
    prompts = []

    def fake_call(model, system_prompt, user_content, max_tokens, timeout_seconds):
        prompts.append(system_prompt)
        return next(responses)

    monkeypatch.setattr(grammar_fix, "_call_flm_api", fake_call)

    text, _, _, strategy = grammar_fix.call_flm("prompt", "Task: Build a plan")

    assert strategy == "prompt_short"
    assert text == "Create a concrete execution plan with deliverables."
    assert any("meta-framing" in prompt for prompt in prompts)


def test_prompt_mode_cli_writes_output_file(fresh_modules, monkeypatch, tmp_path: Path):
    grammar_fix = fresh_modules("grammar_fix")
    monkeypatch.setattr(grammar_fix, "is_flm_server_reachable", lambda: True)

    def fake_call(model, system_prompt, user_content, max_tokens, timeout_seconds):
        # v1.3.0 tightened the prompt-mode system prompt; "Claude-ready" is the
        # remaining signal that the prompt-mode path was selected.
        assert "Claude-ready" in system_prompt
        return ("<task>Refine onboarding email</task>", model)

    monkeypatch.setattr(grammar_fix, "_call_flm_api", fake_call)

    in_path = tmp_path / "in.txt"
    out_path = tmp_path / "out.txt"
    in_path.write_text("refine onboarding email", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "grammar_fix.py",
            "--mode",
            "prompt",
            "--input-file",
            str(in_path),
            "--output-file",
            str(out_path),
        ],
    )
    grammar_fix.main()
    assert "<task>" in out_path.read_text(encoding="utf-8")


def test_call_flm_prompt_rejects_prompt_colon_echo(fresh_modules, monkeypatch):
    grammar_fix = fresh_modules("grammar_fix")
    monkeypatch.setattr(grammar_fix, "is_flm_server_reachable", lambda: True)
    echo = "Prompt: Develop an app for Java that plays a game of ducks."

    def fake_call(model, system_prompt, user_content, max_tokens, timeout_seconds):
        return (echo, grammar_fix.FLM_MODEL)

    monkeypatch.setattr(grammar_fix, "_call_flm_api", fake_call)

    text, _, _, _ = grammar_fix.call_flm("prompt", "Develop a app for java that play a game of ducks")

    assert text.startswith("<task>")
    assert not text.lower().startswith("prompt:")


def test_call_flm_prompt_falls_back_to_force_shape_when_rescue_fails(fresh_modules, monkeypatch):
    grammar_fix = fresh_modules("grammar_fix")
    monkeypatch.setattr(grammar_fix, "is_flm_server_reachable", lambda: True)

    def fake_call(model, system_prompt, user_content, max_tokens, timeout_seconds):
        if "Claude-ready prompt for Anthropic" in system_prompt:
            raise RuntimeError("rescue failed")
        return ("just copy me", model)

    monkeypatch.setattr(grammar_fix, "_call_flm_api", fake_call)

    text, _, _, _ = grammar_fix.call_flm("prompt", "just copy me")

    assert text.startswith("<task>")


def test_apply_config_patch_updates_flm_model_and_runtime(fresh_modules, monkeypatch):
    grammar_fix = fresh_modules("grammar_fix")
    monkeypatch.setattr(
        grammar_fix,
        "list_flm_models",
        lambda: {"models": ["qwen3.5:4b", "other:1b"], "active": "qwen3.5:4b"},
    )
    monkeypatch.setattr(grammar_fix, "_warmup_request", lambda model: None)
    monkeypatch.setattr(grammar_fix, "stop_flm_server", lambda force=True: True)
    monkeypatch.setattr(grammar_fix, "start_flm_server", lambda force_restart=False: "started")

    result = grammar_fix.apply_config_patch({"flm_config": {"active_model": "other:1b"}})

    assert result == "model=other:1b restarted"
    assert grammar_fix.FLM_MODEL == "other:1b"
    saved = json.loads(grammar_fix.CONFIG_PATH.read_text(encoding="utf-8"))
    assert saved["flm_config"]["active_model"] == "other:1b"


def test_apply_config_patch_rejects_uninstalled_model(fresh_modules, monkeypatch):
    grammar_fix = fresh_modules("grammar_fix")
    monkeypatch.setattr(
        grammar_fix,
        "list_flm_models",
        lambda: {"models": ["qwen3.5:4b"], "active": "qwen3.5:4b"},
    )

    with pytest.raises(RuntimeError, match="not installed"):
        grammar_fix.apply_config_patch({"flm_config": {"active_model": "missing:7b"}})


def test_apply_config_patch_syncs_chat_llm_model(fresh_modules, monkeypatch):
    grammar_fix = fresh_modules("grammar_fix")
    cfg = grammar_fix.load_config()
    cfg["chat_config"] = {"llm_model": "stale:old"}
    grammar_fix.save_config(cfg)
    monkeypatch.setattr(
        grammar_fix,
        "list_flm_models",
        lambda: {"models": ["qwen3.5:4b", "other:1b"], "active": "qwen3.5:4b"},
    )
    monkeypatch.setattr(grammar_fix, "_warmup_request", lambda model: None)
    monkeypatch.setattr(grammar_fix, "stop_flm_server", lambda force=True: True)
    monkeypatch.setattr(grammar_fix, "start_flm_server", lambda force_restart=False: "started")

    grammar_fix.apply_config_patch({"flm_config": {"active_model": "other:1b"}})

    saved = json.loads(grammar_fix.CONFIG_PATH.read_text(encoding="utf-8"))
    assert saved["flm_config"]["active_model"] == "other:1b"
    assert "llm_model" not in (saved.get("chat_config") or {})


def test_apply_config_patch_restarts_flm_server_on_model_change(fresh_modules, monkeypatch):
    grammar_fix = fresh_modules("grammar_fix")
    monkeypatch.setattr(
        grammar_fix,
        "list_flm_models",
        lambda: {"models": ["qwen3.5:4b", "other:1b"], "active": "qwen3.5:4b"},
    )

    call_log: list[tuple[str, object]] = []

    def fake_stop(force=True):
        call_log.append(("stop", force))

    def fake_start(force_restart=True):
        call_log.append(("start", force_restart))
        return "started"

    monkeypatch.setattr(grammar_fix, "stop_flm_server", fake_stop)
    monkeypatch.setattr(grammar_fix, "start_flm_server", fake_start)

    result = grammar_fix.apply_config_patch({"flm_config": {"active_model": "other:1b"}})

    assert result == "model=other:1b restarted"
    assert [name for name, _ in call_log] == ["stop", "start"]
    assert call_log[0][1] is True
    assert call_log[1][1] is True
    assert grammar_fix.FLM_MODEL == "other:1b"


def test_apply_config_patch_start_failure_is_non_fatal(fresh_modules, monkeypatch, caplog):
    grammar_fix = fresh_modules("grammar_fix")
    monkeypatch.setattr(
        grammar_fix,
        "list_flm_models",
        lambda: {"models": ["qwen3.5:4b", "other:1b"], "active": "qwen3.5:4b"},
    )
    monkeypatch.setattr(grammar_fix, "stop_flm_server", lambda force=True: True)

    def start_raises(**kwargs):
        raise RuntimeError("simulated start failure")

    monkeypatch.setattr(grammar_fix, "start_flm_server", start_raises)

    caplog.set_level("WARNING", logger="flowkey.grammar_fix")
    result = grammar_fix.apply_config_patch({"flm_config": {"active_model": "other:1b"}})

    assert result == "model=other:1b restarted"
    assert grammar_fix.FLM_MODEL == "other:1b"


def test_apply_config_patch_does_not_restart_when_model_unchanged(fresh_modules, monkeypatch):
    grammar_fix = fresh_modules("grammar_fix")
    monkeypatch.setattr(
        grammar_fix,
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

    monkeypatch.setattr(grammar_fix, "stop_flm_server", fake_stop)
    monkeypatch.setattr(grammar_fix, "start_flm_server", fake_start)

    result = grammar_fix.apply_config_patch({"flm_serving_config": {"auto_start": False}})

    assert result == "ok"
    assert restart_called == {"stop": 0, "start": 0}


def test_apply_config_patch_rejects_embedding_model(fresh_modules, monkeypatch):
    grammar_fix = fresh_modules("grammar_fix")
    list_calls = {"n": 0}

    def fake_list():
        list_calls["n"] += 1
        # Simulate the embedding model being installed — the new chat-selectable
        # check must still reject it before this list is consulted.
        return {"models": ["qwen3.5:4b", "embed-gemma:300m"], "active": "qwen3.5:4b"}

    monkeypatch.setattr(grammar_fix, "list_flm_models", fake_list)
    monkeypatch.setattr(grammar_fix, "stop_flm_server", lambda force=True: True)
    monkeypatch.setattr(grammar_fix, "start_flm_server", lambda force_restart=False: "started")

    with pytest.raises(RuntimeError, match="not a chat-selectable model"):
        grammar_fix.apply_config_patch({"flm_config": {"active_model": "embed-gemma:300m"}})

    # The file on disk is untouched (no half-write).
    on_disk = json.loads(grammar_fix.CONFIG_PATH.read_text(encoding="utf-8"))
    assert on_disk.get("flm_config", {}).get("active_model") != "embed-gemma:300m"


def test_apply_config_patch_rejects_asr_only_model(fresh_modules, monkeypatch):
    grammar_fix = fresh_modules("grammar_fix")
    monkeypatch.setattr(
        grammar_fix,
        "list_flm_models",
        lambda: {"models": ["qwen3.5:4b", "whisper-v3:turbo"], "active": "qwen3.5:4b"},
    )

    with pytest.raises(RuntimeError, match="not a chat-selectable model"):
        grammar_fix.apply_config_patch({"flm_config": {"active_model": "whisper-v3:turbo"}})


def test_refresh_runtime_config_falls_back_when_model_is_non_chat(
    fresh_modules, monkeypatch, caplog
):
    grammar_fix = fresh_modules("grammar_fix")
    grammar_fix.CONFIG_PATH.write_text(
        json.dumps({"flm_config": {"active_model": "embed-gemma:300m"}}),
        encoding="utf-8",
    )

    caplog.set_level("WARNING", logger="flowkey.grammar_fix")
    grammar_fix.refresh_runtime_config()

    assert grammar_fix.FLM_MODEL == "gemma4-it:e4b"
    assert any("embed-gemma:300m" in rec.message for rec in caplog.records)
    assert any("not a chat-selectable" in rec.message for rec in caplog.records)


def test_read_and_write_output_file_modes(fresh_modules, monkeypatch, tmp_path: Path):
    grammar_fix = fresh_modules("grammar_fix")
    input_path = tmp_path / "input.txt"
    output_path = tmp_path / "output.txt"
    input_path.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(
        grammar_fix.sys,
        "argv",
        ["grammar_fix.py", "--input-file", str(input_path), "--output-file", str(output_path)],
    )

    assert grammar_fix._read_input_text() == "hello"
    grammar_fix._write_output_text("world")
    assert output_path.read_text(encoding="utf-8") == "world"
