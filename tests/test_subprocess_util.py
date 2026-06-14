from __future__ import annotations

from types import SimpleNamespace

import subprocess_util


def test_run_flm_strips_bundle_library_path(monkeypatch):
    capture: dict = {}

    def _fake_run(argv, **kwargs):
        capture["argv"] = list(argv)
        capture["env"] = dict(kwargs.get("env") or {})
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess_util.subprocess, "run", _fake_run)

    env = {
        "CUSTOM": "1",
        "LD_LIBRARY_PATH": "/home/j/.local/opt/flowkey/current/_internal",
        "LD_PRELOAD": "libsomething.so",
    }

    subprocess_util.run_flm(["flm", "version"], env=env)

    assert capture["argv"] == ["flm", "version"]
    assert capture["env"] == {"CUSTOM": "1"}
    assert env["LD_LIBRARY_PATH"] == "/home/j/.local/opt/flowkey/current/_internal"


def test_popen_flm_strips_bundle_library_path(monkeypatch):
    capture: dict = {}

    def _fake_popen(argv, **kwargs):
        capture["argv"] = list(argv)
        capture["env"] = dict(kwargs.get("env") or {})
        return SimpleNamespace(pid=12345)

    monkeypatch.setattr(subprocess_util.subprocess, "Popen", _fake_popen)

    env = {
        "PATH": "/usr/bin",
        "LD_LIBRARY_PATH": "/home/j/.local/opt/flowkey/current/_internal",
    }

    proc = subprocess_util.popen_flm(["flm", "pull", "gemma4-it:e4b"], env=env)

    assert proc.pid == 12345
    assert capture["argv"] == ["flm", "pull", "gemma4-it:e4b"]
    assert capture["env"] == {"PATH": "/usr/bin"}
