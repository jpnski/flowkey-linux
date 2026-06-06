from __future__ import annotations

import json
import types

import flm_server


def _fake_run(stdout="", returncode=0, stderr="", capture=None):
    """Return a run_hidden stand-in yielding a fixed CompletedProcess-like object."""
    def _run(argv, **kwargs):
        if capture is not None:
            capture["argv"] = list(argv)
            capture["kwargs"] = dict(kwargs)
        return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    return _run


# Mirrors the real `flm list --json` shape: each model object carries an
# authoritative `installed` boolean. Two installed, two not.
SAMPLE = json.dumps({
    "models": [
        {"model": "qwen3.5:4b",      "name": "qwen3.5:4b",      "installed": True},
        {"model": "llama3.2:3b",     "name": "llama3.2:3b",     "installed": True},
        {"model": "gpt-oss:20b",     "name": "gpt-oss:20b",     "installed": False},
        {"model": "phi4-mini-it:4b",                            "installed": False},
    ]
})


def test_installed_returns_only_installed_clean_names(monkeypatch):
    monkeypatch.setattr(flm_server, "run_hidden", _fake_run(stdout=SAMPLE))
    out = flm_server.flm_list("installed", "qwen3.5:4b")
    assert out["models"] == ["qwen3.5:4b", "llama3.2:3b"]
    assert out["active"] == "qwen3.5:4b"
    assert out.get("error") is None


def test_not_installed_returns_only_not_installed(monkeypatch):
    monkeypatch.setattr(flm_server, "run_hidden", _fake_run(stdout=SAMPLE))
    out = flm_server.flm_list("not-installed", "qwen3.5:4b")
    assert out["models"] == ["gpt-oss:20b", "phi4-mini-it:4b"]


def test_all_returns_every_model(monkeypatch):
    monkeypatch.setattr(flm_server, "run_hidden", _fake_run(stdout=SAMPLE))
    out = flm_server.flm_list("all", "x")
    assert out["models"] == ["qwen3.5:4b", "llama3.2:3b", "gpt-oss:20b", "phi4-mini-it:4b"]


def test_uses_json_mode_not_quiet_text(monkeypatch):
    cap = {}
    monkeypatch.setattr(flm_server, "run_hidden", _fake_run(stdout=SAMPLE, capture=cap))
    flm_server.flm_list("installed", "x")
    assert "--json" in cap["argv"]
    assert "--quiet" not in cap["argv"]
    assert "--filter" not in cap["argv"]
    assert cap["kwargs"].get("encoding") == "utf-8"


def test_tolerates_non_json_preamble(monkeypatch):
    monkeypatch.setattr(flm_server, "run_hidden", _fake_run(stdout="loading models...\n" + SAMPLE))
    out = flm_server.flm_list("installed", "x")
    assert out["models"] == ["qwen3.5:4b", "llama3.2:3b"]


def test_decorated_text_without_json_yields_error_not_bogus_models(monkeypatch):
    bad = "Models:\n  - qwen3.5:4b\n  No models found for the specified filter.\n"
    monkeypatch.setattr(flm_server, "run_hidden", _fake_run(stdout=bad))
    out = flm_server.flm_list("installed", "x")
    assert out["models"] == []
    assert "could not parse" in (out.get("error") or "")


def test_nonzero_exit_returns_error(monkeypatch):
    monkeypatch.setattr(flm_server, "run_hidden", _fake_run(returncode=1, stderr="boom"))
    out = flm_server.flm_list("installed", "x")
    assert out["models"] == []
    assert out["error"] == "boom"


def test_missing_cli_returns_error(monkeypatch):
    def _raise(*_a, **_k):
        raise FileNotFoundError()
    monkeypatch.setattr(flm_server, "run_hidden", _raise)
    out = flm_server.flm_list("installed", "x")
    assert out["models"] == []
    assert "not found" in out["error"]


def test_bad_filter_rejected_before_subprocess(monkeypatch):
    called = {"n": 0}
    def _run(*_a, **_k):
        called["n"] += 1
        return types.SimpleNamespace(returncode=0, stdout=SAMPLE, stderr="")
    monkeypatch.setattr(flm_server, "run_hidden", _run)
    out = flm_server.flm_list("bogus", "x")
    assert "bad filter" in out["error"]
    assert called["n"] == 0  # rejected without shelling out
