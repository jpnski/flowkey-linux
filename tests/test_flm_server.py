from __future__ import annotations

import json
import types

import flm_server


def _fake_run(stdout="", returncode=0, stderr="", capture=None):
    """Return a run_captured stand-in yielding a fixed CompletedProcess-like object."""
    def _run(argv, **kwargs):
        if capture is not None:
            capture["argv"] = list(argv)
            capture["kwargs"] = dict(kwargs)
        return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    return _run


# Mirrors the real `flm list --json` shape. Each entry carries the metadata
# that `get_all_models()` in FLM passes through (`label`, `details.family`).
# Non-chat entries (embed-gemma, whisper-v3) are present and *installed*; the
# helper must still drop them from every filter result.
SAMPLE = json.dumps({
    "models": [
        {"model": "qwen3.5:4b",      "name": "qwen3.5:4b",      "installed": True,
         "label": ["vision", "reasoning"], "details": {"family": "qwen3.5"}},
        {"model": "llama3.2:3b",     "name": "llama3.2:3b",     "installed": True,
         "label": ["reasoning", "tool-calling"], "details": {"family": "llama3"}},
        {"model": "gpt-oss:20b",     "name": "gpt-oss:20b",     "installed": False,
         "label": ["reasoning"], "details": {"family": "gpt-oss"}},
        {"model": "phi4-mini-it:4b", "installed": False,
         "label": ["reasoning", "tool-calling"], "details": {"family": "phi4"}},
        {"model": "embed-gemma:300m", "name": "embed-gemma:300m", "installed": True,
         "label": ["embeddings"], "details": {"family": "embed-gemma"}},
        {"model": "whisper-v3:turbo", "name": "whisper-v3:turbo", "installed": True,
         "label": ["audio", "realtime-transcription", "transcription"],
         "details": {"family": "whisper-v3"}},
    ]
})


def test_installed_returns_only_installed_clean_names(monkeypatch):
    monkeypatch.setattr(flm_server, "run_captured", _fake_run(stdout=SAMPLE))
    out = flm_server.flm_list("installed", "qwen3.5:4b")
    assert out["models"] == ["qwen3.5:4b", "llama3.2:3b"]
    assert out["active"] == "qwen3.5:4b"
    assert out.get("error") is None


def test_not_installed_returns_only_not_installed(monkeypatch):
    monkeypatch.setattr(flm_server, "run_captured", _fake_run(stdout=SAMPLE))
    out = flm_server.flm_list("not-installed", "qwen3.5:4b")
    assert out["models"] == ["gpt-oss:20b", "phi4-mini-it:4b"]


def test_all_returns_every_model(monkeypatch):
    monkeypatch.setattr(flm_server, "run_captured", _fake_run(stdout=SAMPLE))
    out = flm_server.flm_list("all", "x")
    assert out["models"] == ["qwen3.5:4b", "llama3.2:3b", "gpt-oss:20b", "phi4-mini-it:4b"]


def test_uses_json_mode_not_quiet_text(monkeypatch):
    cap = {}
    monkeypatch.setattr(flm_server, "run_captured", _fake_run(stdout=SAMPLE, capture=cap))
    flm_server.flm_list("installed", "x")
    assert "--json" in cap["argv"]
    assert "--quiet" not in cap["argv"]
    assert "--filter" not in cap["argv"]
    assert cap["kwargs"].get("encoding") == "utf-8"


def test_tolerates_non_json_preamble(monkeypatch):
    monkeypatch.setattr(flm_server, "run_captured", _fake_run(stdout="loading models...\n" + SAMPLE))
    out = flm_server.flm_list("installed", "x")
    assert out["models"] == ["qwen3.5:4b", "llama3.2:3b"]


def test_decorated_text_without_json_yields_error_not_bogus_models(monkeypatch):
    bad = "Models:\n  - qwen3.5:4b\n  No models found for the specified filter.\n"
    monkeypatch.setattr(flm_server, "run_captured", _fake_run(stdout=bad))
    out = flm_server.flm_list("installed", "x")
    assert out["models"] == []
    assert "could not parse" in (out.get("error") or "")


def test_nonzero_exit_returns_error(monkeypatch):
    monkeypatch.setattr(flm_server, "run_captured", _fake_run(returncode=1, stderr="boom"))
    out = flm_server.flm_list("installed", "x")
    assert out["models"] == []
    assert out["error"] == "boom"


def test_missing_cli_returns_error(monkeypatch):
    def _raise(*_a, **_k):
        raise FileNotFoundError()
    monkeypatch.setattr(flm_server, "run_captured", _raise)
    out = flm_server.flm_list("installed", "x")
    assert out["models"] == []
    assert "not found" in out["error"]


def test_bad_filter_rejected_before_subprocess(monkeypatch):
    called = {"n": 0}
    def _run(*_a, **_k):
        called["n"] += 1
        return types.SimpleNamespace(returncode=0, stdout=SAMPLE, stderr="")
    monkeypatch.setattr(flm_server, "run_captured", _run)
    out = flm_server.flm_list("bogus", "x")
    assert "bad filter" in out["error"]
    assert called["n"] == 0  # rejected without shelling out


def test_filters_embedding_and_asr_models_using_metadata(monkeypatch):
    """embed-gemma:300m and whisper-v3:turbo must never appear in any filter result."""
    monkeypatch.setattr(flm_server, "run_captured", _fake_run(stdout=SAMPLE))

    for kind in ("installed", "not-installed", "all"):
        out = flm_server.flm_list(kind, "x")
        assert "embed-gemma:300m" not in out["models"], f"leaked in {kind}"
        assert "whisper-v3:turbo" not in out["models"], f"leaked in {kind}"


def test_falls_back_to_name_prefix_when_metadata_missing(monkeypatch):
    """If the catalog strips `label`/`details.family`, the name-prefix fallback still drops `embed-*` / `whisper-*`."""
    sparse = json.dumps({
        "models": [
            {"model": "qwen3.5:4b", "installed": True},
            {"model": "embed-foo:7b", "installed": True},
            {"model": "whisper-xx:1b", "installed": True},
        ]
    })
    monkeypatch.setattr(flm_server, "run_captured", _fake_run(stdout=sparse))
    out = flm_server.flm_list("installed", "x")
    assert out["models"] == ["qwen3.5:4b"]


def test_is_selectable_chat_model_helper_unit():
    # Direct unit checks on the helper to lock in the smart rule.
    chat_entry = {"details": {"family": "gemma4e"}, "label": ["reasoning", "vision"]}
    assert flm_server._is_selectable_chat_model("gemma4-it:e4b", chat_entry) is True

    embed_entry = {"details": {"family": "embed-gemma"}, "label": ["embeddings"]}
    assert flm_server._is_selectable_chat_model("embed-gemma:300m", embed_entry) is False

    asr_entry = {
        "details": {"family": "whisper-v3"},
        "label": ["audio", "realtime-transcription", "transcription"],
    }
    assert flm_server._is_selectable_chat_model("whisper-v3:turbo", asr_entry) is False

    # Family-only signal (no labels): still classified by family.
    asr_family_only = {"details": {"family": "whisper-v3"}}
    assert flm_server._is_selectable_chat_model("whisper-v3:turbo", asr_family_only) is False

    # No metadata at all → name-prefix fallback.
    assert flm_server._is_selectable_chat_model("embed-mystery:1b") is False
    assert flm_server._is_selectable_chat_model("whisper-xx:1b") is False
    assert flm_server._is_selectable_chat_model("qwen3.5:4b") is True

    # Empty / falsy name is never selectable.
    assert flm_server._is_selectable_chat_model("") is False
    assert flm_server._is_selectable_chat_model(None) is False  # type: ignore[arg-type]
