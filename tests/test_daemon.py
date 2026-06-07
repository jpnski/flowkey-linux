from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest


def _read_json(url: str, method: str = "GET", body: bytes | None = None, headers: dict | None = None):
    # The daemon now requires the X-FFP-API header on POSTs (CSRF defense); send
    # it by default. Callers can override/clear via the headers arg.
    hdrs = {"X-FFP-API": "1"}
    if headers is not None:
        hdrs = headers
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


@pytest.fixture
def daemon_module(fresh_modules):
    module = fresh_modules("daemon")
    module._shutdown_event = threading.Event()
    return module


@pytest.fixture
def daemon_server(daemon_module):
    server = daemon_module.ThreadingHTTPServer((daemon_module.HOST, 0), daemon_module.Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    base_url = f"http://{daemon_module.HOST}:{server.server_port}"
    try:
        yield daemon_module, base_url
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        daemon_module._shutdown_event = threading.Event()


def test_actions_count_and_expected_names(daemon_module):
    # v1.4.0 added get_autostart_state + set_autostart -> 38.
    # Late v1.4.0 added flm_update_check, bench_start/status/history,
    # v2.0.0: removed chat_reload, chat_restart (chat_popup deleted) -> 46.
    # v2.0.0+flm-panel: added pull_cancel -> 47.
    assert len(daemon_module.ACTIONS) == 47
    assert "version" in daemon_module.ACTIONS
    assert "apply_config_patch" in daemon_module.ACTIONS
    assert "chat_send_selection" in daemon_module.ACTIONS
    assert "open_dashboard" in daemon_module.ACTIONS
    assert "config_snapshot" in daemon_module.ACTIONS
    assert "get_autostart_state" in daemon_module.ACTIONS
    assert "set_autostart" in daemon_module.ACTIONS
    # late-v1.4.0 feature actions
    assert "flm_update_check" in daemon_module.ACTIONS
    assert "bench_start" in daemon_module.ACTIONS
    assert "bench_status" in daemon_module.ACTIONS
    assert "bench_history" in daemon_module.ACTIONS
    assert "note_search" in daemon_module.ACTIONS
    assert "pull_start" in daemon_module.ACTIONS
    assert "pull_status" in daemon_module.ACTIONS
    assert "pull_cancel" in daemon_module.ACTIONS
    # removed in v1.5.4
    assert "model_stats" not in daemon_module.ACTIONS


def test_write_actions_include_mutating_routes(daemon_module):
    assert "apply_config_patch" in daemon_module._WRITE_ACTIONS
    assert "pull_model" in daemon_module._WRITE_ACTIONS
    assert "set_tone_formal" in daemon_module._WRITE_ACTIONS
    assert "version" not in daemon_module._WRITE_ACTIONS


def test_ok_envelope_shape(daemon_module):
    result = daemon_module._ok({"x": 1}, 12.345)

    assert result == {"ok": True, "result": {"x": 1}, "error": None, "elapsed_ms": 12.35}


def test_err_envelope_shape(daemon_module):
    result = daemon_module._err("broken", 12.345)

    assert result == {"ok": False, "result": None, "error": "broken", "elapsed_ms": 12.35}


def test_xml_escape_neutralizes_injection(daemon_module):
    # Toast text must not be able to break out of the single-quoted PowerShell
    # here-string in _show_toast_async: apostrophe + newline are the escape risks.
    out = daemon_module._xml_escape("a'@\n<b>&\"x")
    assert "'" not in out
    assert "\n" not in out
    assert "<" not in out and ">" not in out
    assert "&apos;" in out and "&lt;" in out and "&quot;" in out


def test_healthz_reports_actions(daemon_server):
    daemon_module, base_url = daemon_server

    status, payload = _read_json(base_url + "/healthz")

    assert status == 200
    assert payload["ok"] is True
    assert payload["version"] == daemon_module.grammar_fix.APP_VERSION
    assert "version" in payload["actions"]


def test_unknown_get_path_returns_404(daemon_server):
    _, base_url = daemon_server

    with pytest.raises(urllib.error.HTTPError) as exc:
        _read_json(base_url + "/nope")

    assert exc.value.code == 404


def test_post_without_api_header_is_rejected(daemon_server):
    # CSRF defense: a POST lacking the X-FFP-API header must be refused (403),
    # so a cross-origin web page cannot trigger actions on the localhost daemon.
    _, base_url = daemon_server

    with pytest.raises(urllib.error.HTTPError) as exc:
        _read_json(base_url + "/action/version", method="POST", body=b"{}", headers={})

    assert exc.value.code == 403


def test_post_version_returns_app_version(daemon_server):
    daemon_module, base_url = daemon_server

    status, payload = _read_json(base_url + "/action/version", method="POST", body=b"{}")

    assert status == 200
    assert payload["ok"] is True
    assert payload["result"] == daemon_module.grammar_fix.APP_VERSION


def test_post_stats_returns_expected_shape(daemon_server):
    _, base_url = daemon_server

    status, payload = _read_json(base_url + "/action/stats", method="POST", body=b"{}")

    assert status == 200
    assert set(payload["result"]) >= {
        "total",
        "by_mode",
        "avg_latency_seconds",
        "p50_latency_seconds",
        "p95_latency_seconds",
        "avg_tok_per_sec",
        "p50_tok_per_sec",
        "total_prompt_tokens",
        "total_completion_tokens",
    }


def test_post_dashboard_data_returns_expected_shape(daemon_server):
    _, base_url = daemon_server

    status, payload = _read_json(base_url + "/action/dashboard_data", method="POST", body=b"{}")

    assert status == 200
    assert set(payload["result"]) == {"latencies_recent", "hour_buckets"}


def test_post_config_snapshot_returns_flat_dashboard_fields(daemon_server):
    _, base_url = daemon_server

    status, payload = _read_json(base_url + "/action/config_snapshot", method="POST", body=b"{}")

    assert status == 200
    assert set(payload["result"]) >= {
        "version",
        "flm_base_url",
        "flm_model",
        "flm_timeout_seconds",
        "history_store_text",
        "server",
        "routing",
        "notes",
        "tone",
        "hotkeys",
    }
    notes = payload["result"]["notes"]
    assert isinstance(notes.get("categories"), list)


def test_apply_config_patch_persists_nested_value(daemon_server):
    daemon_module, base_url = daemon_server
    body = json.dumps({"args": {"patch": {"server": {"auto_start": False}}}}).encode("utf-8")

    status, payload = _read_json(base_url + "/action/apply_config_patch", method="POST", body=body)

    saved = json.loads(daemon_module.grammar_fix.CONFIG_PATH.read_text(encoding="utf-8"))
    assert status == 200
    assert payload["result"] == "ok"
    assert saved["server"]["auto_start"] is False


def test_apply_config_patch_model_change_validates_and_persists(daemon_server, monkeypatch):
    daemon_module, base_url = daemon_server
    monkeypatch.setattr(
        daemon_module.grammar_fix,
        "list_flm_models",
        lambda: {"models": ["qwen3.5:4b", "other:1b"], "active": "qwen3.5:4b"},
    )
    monkeypatch.setattr(daemon_module.grammar_fix, "_warmup_request", lambda model: None)
    monkeypatch.setattr(daemon_module.grammar_fix, "stop_flm_server", lambda force=True: True)
    monkeypatch.setattr(daemon_module.grammar_fix, "start_flm_server", lambda force_restart=False: "started")
    body = json.dumps({"args": {"patch": {"flm_model": "other:1b"}}}).encode("utf-8")

    status, payload = _read_json(base_url + "/action/apply_config_patch", method="POST", body=body)

    saved = json.loads(daemon_module.grammar_fix.CONFIG_PATH.read_text(encoding="utf-8"))
    assert status == 200
    assert payload["result"] == "model=other:1b restarted"
    assert saved["flm_model"] == "other:1b"
    assert daemon_module.grammar_fix.FLM_MODEL == "other:1b"


def test_apply_config_patch_supports_file_argument(daemon_server, tmp_path):
    _, base_url = daemon_server
    patch_path = tmp_path / "patch.json"
    patch_path.write_text(json.dumps({"enabled": False}), encoding="utf-8")
    body = json.dumps({"args": {"file": str(patch_path)}}).encode("utf-8")

    status, payload = _read_json(base_url + "/action/apply_config_patch", method="POST", body=body)

    assert status == 200
    assert payload["result"] == "ok"


def test_unknown_action_returns_404(daemon_server):
    _, base_url = daemon_server

    with pytest.raises(urllib.error.HTTPError) as exc:
        _read_json(base_url + "/action/not_real", method="POST", body=b"{}")

    assert exc.value.code == 404


def test_invalid_json_body_returns_400(daemon_server):
    _, base_url = daemon_server
    req = urllib.request.Request(
        base_url + "/action/version",
        data=b"{bad",
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8", "X-FFP-API": "1"},
    )

    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)

    assert exc.value.code == 400


def test_non_utf8_body_returns_400(daemon_server):
    _, base_url = daemon_server
    req = urllib.request.Request(
        base_url + "/action/version",
        data=b"\x80\x81",
        method="POST",
        headers={"Content-Type": "application/json", "X-FFP-API": "1"},
    )

    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)

    assert exc.value.code == 400


def test_shutdown_action_sets_event(daemon_server):
    daemon_module, base_url = daemon_server

    status, payload = _read_json(base_url + "/action/shutdown", method="POST", body=b"{}")
    time.sleep(0.1)

    assert status == 200
    assert payload["ok"] is True
    assert daemon_module._shutdown_event.is_set() is True


def test_models_list_can_be_stubbed_via_action(daemon_server, monkeypatch):
    daemon_module, base_url = daemon_server
    monkeypatch.setattr(
        daemon_module.grammar_fix,
        "list_flm_models",
        lambda: {"models": ["qwen3.5:4b"], "active": "qwen3.5:4b"},
    )

    status, payload = _read_json(base_url + "/action/models_list", method="POST", body=b"{}")

    assert status == 200
    assert payload["result"]["active"] == "qwen3.5:4b"


def test_set_tone_rejects_unknown_preset(daemon_module):
    with pytest.raises(ValueError):
        daemon_module._act_set_tone({"preset": "loud"})


def test_open_dashboard_writes_marker(daemon_server, tmp_path, monkeypatch):
    daemon_module, base_url = daemon_server
    monkeypatch.setattr(daemon_module._paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(daemon_module._paths, "MARKER_OPEN_DASHBOARD", tmp_path / ".open_dashboard")

    status, payload = _read_json(base_url + "/action/open_dashboard", method="POST", body=b"{}")

    assert status == 200
    assert payload["ok"] is True
    assert payload["result"] == "queued"


def test_do_post_swallows_broken_pipe_on_success_send(daemon_module, caplog):
    """When the client disconnects between the handler running and the response
    being written, the daemon must not retry a 500 send and must not propagate."""
    handler = daemon_module.Handler.__new__(daemon_module.Handler)
    send_calls: list[tuple[int, dict]] = []

    def fake_send_json(status: int, body: dict) -> None:
        send_calls.append((status, body))
        if status == 200:
            raise BrokenPipeError("client gone")

    handler._send_json = fake_send_json  # type: ignore[method-assign]

    import io
    handler.command = "POST"
    handler.path = "/action/version"
    handler.client_address = ("127.0.0.1", 0)
    handler.headers = {"X-FFP-API": "1", "Content-Length": str(len(b"{}"))}
    handler.rfile = io.BytesIO(b"{}")

    caplog.set_level("INFO", logger="flowkey.daemon")
    handler.do_POST()  # type: ignore[arg-type]

    statuses = [s for s, _ in send_calls]
    assert statuses == [200]
    assert any("client disconnected" in rec.message for rec in caplog.records)
    assert all(rec.levelname == "INFO" for rec in caplog.records if "client disconnected" in rec.message)


def test_send_json_safely_returns_false_on_disconnect(daemon_module):
    handler = daemon_module.Handler.__new__(daemon_module.Handler)

    def raise_reset(status: int, body: dict) -> None:
        raise ConnectionResetError("reset")

    handler._send_json = raise_reset  # type: ignore[method-assign]
    assert handler._send_json_safely(200, {"ok": True}) is False

    def raise_bp(status: int, body: dict) -> None:
        raise BrokenPipeError("gone")

    handler._send_json = raise_bp  # type: ignore[method-assign]
    assert handler._send_json_safely(500, {"ok": False}) is False


def test_send_json_safely_returns_true_on_normal_send(daemon_module):
    handler = daemon_module.Handler.__new__(daemon_module.Handler)
    sent: list[tuple[int, dict]] = []
    handler._send_json = lambda status, body: sent.append((status, body))  # type: ignore[method-assign]
    assert handler._send_json_safely(200, {"ok": True, "result": "x"}) is True
    assert sent == [(200, {"ok": True, "result": "x"})]



