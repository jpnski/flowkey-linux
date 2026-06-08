"""Tests for scripts/pull.py — async `flm pull` state machine + cancel."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import pytest


def _wait_for_state(pull, target: str, *, timeout: float = 2.0) -> dict:
    """Poll pull.status() until state matches `target` (or timeout)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = pull.status()
        if s["state"] == target:
            return s
        time.sleep(0.02)
    pytest.fail(f"pull state never reached {target!r}; last={pull.status()!r}")


def test_status_idle_initially(fresh_modules):
    pull = fresh_modules("pull")
    s = pull.status()
    assert s["state"] == "idle"
    assert s["model"] == ""
    assert s["percent"] == 0.0


def test_cancel_pull_returns_error_when_idle(fresh_modules):
    pull = fresh_modules("pull")
    resp = pull.cancel_pull()
    assert resp["ok"] is False
    assert resp["state"] == "idle"
    assert "no pull in progress" in resp["error"]


def test_start_pull_refuses_second_concurrent(fresh_modules):
    pull = fresh_modules("pull")

    blocker = threading.Event()

    def runner(model: str, on_line: Callable[[str], None]) -> int:
        on_line("0%")
        blocker.wait(timeout=5)
        return 0

    first = pull.start_pull("qwen3.5:4b", runner=runner)
    assert first["ok"] is True
    _wait_for_state(pull, "running")

    try:
        second = pull.start_pull("llama3:8b", runner=runner)
        assert second["ok"] is False
        assert "already running" in second["error"]
        assert second["model"] == "qwen3.5:4b"
    finally:
        blocker.set()
        _wait_for_state(pull, "done")


def test_cancel_pull_terminates_running_pull(fresh_modules, monkeypatch):
    pull = fresh_modules("pull")
    # Suppress filesystem side-effects — the daemon-worker invokes
    # _cleanup_partial_model after detecting cancellation, but in unit
    # tests there's no real flm model to clean up.
    monkeypatch.setattr(pull, "_cleanup_partial_model", lambda m: None)

    cancel_flag = threading.Event()
    started = threading.Event()

    def runner(model: str, on_line: Callable[[str], None]) -> int:
        on_line("5%")
        started.set()
        # Block until cancelled (or 5s safety timeout).
        cancel_flag.wait(timeout=5)
        # Returning a non-zero rc is normal for a SIGTERM'd process.
        return 130

    resp = pull.start_pull("qwen3.5:4b", runner=runner)
    assert resp["ok"] is True
    assert started.wait(timeout=2), "runner did not start"
    _wait_for_state(pull, "running")

    cancel = pull.cancel_pull()
    assert cancel["ok"] is True
    assert cancel["state"] == "cancelled"
    assert cancel["model"] == "qwen3.5:4b"

    # Let the runner return; the worker should observe "cancelled" and
    # leave the state alone (not clobber it with "error").
    cancel_flag.set()
    final = _wait_for_state(pull, "cancelled", timeout=2)
    assert final["state"] == "cancelled"
    assert final["model"] == "qwen3.5:4b"
    assert "cancelled" in final["message"].lower() or "cancelled" in final.get("message", "").lower() or final.get("error", "") == ""


def test_cancel_pull_after_done_returns_error(fresh_modules):
    pull = fresh_modules("pull")

    def runner(model: str, on_line: Callable[[str], None]) -> int:
        on_line("100%")
        return 0

    resp = pull.start_pull("qwen3.5:4b", runner=runner)
    assert resp["ok"] is True
    _wait_for_state(pull, "done")

    cancel = pull.cancel_pull()
    assert cancel["ok"] is False
    assert cancel["state"] == "done"
    assert "no pull in progress" in cancel["error"]


def test_cleanup_invoked_on_cancel(fresh_modules, monkeypatch):
    """Verify _cleanup_partial_model is called with the model name on cancel.

    Must synchronise on a separate ``cleanup_done`` event because
    ``cancel_pull()`` sets ``state=cancelled`` immediately (before the worker
    thread processes the cancellation).  Polling the state would see
    ``cancelled`` and return before the cleanup callback fires.
    """
    pull = fresh_modules("pull")
    cleanup_done = threading.Event()
    cleanup_model: list[str] = []

    def tracking_cleanup(model: str) -> None:
        cleanup_model.append(model)
        cleanup_done.set()

    monkeypatch.setattr(pull, "_cleanup_partial_model", tracking_cleanup)

    cancel_flag = threading.Event()
    started = threading.Event()

    def runner(model: str, on_line: Callable[[str], None]) -> int:
        on_line("5%")
        started.set()
        cancel_flag.wait(timeout=5)
        return 130

    resp = pull.start_pull("qwen3.5:4b", runner=runner)
    assert resp["ok"] is True
    assert started.wait(timeout=2), "runner did not start"
    _wait_for_state(pull, "running")

    pull.cancel_pull()
    cancel_flag.set()
    assert cleanup_done.wait(timeout=3), "cleanup was not invoked within timeout"
    assert cleanup_model == ["qwen3.5:4b"], f"got {cleanup_model}"
