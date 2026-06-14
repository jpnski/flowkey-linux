from __future__ import annotations

import json


def test_read_config_loads_new_hotkey_groups(fresh_modules):
    listener = fresh_modules("listener")
    listener._paths.CONFIG_FILE.write_text(
        json.dumps(
            {
                "transform_hotkeys": {
                    "grammar": "Ctrl+Alt+G",
                    "prompt": "Ctrl+Shift+P",
                    "summarize": "Ctrl+Shift+S",
                    "explain": "Ctrl+Shift+E",
                    "tone": "Ctrl+Shift+T",
                },
                "interaction_hotkeys": {
                    "open_chat": "ctrl+alt+t",
                    "ask_chat": "ctrl+alt+a",
                    "capture_note": "ctrl+alt+n",
                },
            }
        ),
        encoding="utf-8",
    )

    cfg = listener.read_config()

    assert cfg.transform_hotkeys.grammar == "Ctrl+Alt+G"
    assert cfg.transform_hotkeys.prompt == "Ctrl+Shift+P"
    assert listener.HOTKEY_BINDINGS["grammar"] == "Ctrl+Alt+G"
    assert listener.HOTKEY_BINDINGS["tone"] == "Ctrl+Shift+T"
    assert listener.HOTKEY_BINDINGS["open_chat"] == "ctrl+alt+t"
    assert listener.HOTKEY_BINDINGS["ask_chat"] == "ctrl+alt+a"


def test_register_hotkeys_from_config_uses_new_action_names(fresh_modules, monkeypatch):
    listener = fresh_modules("listener")
    captured: dict[str, list[str]] = {}

    monkeypatch.setattr(listener, "read_config", lambda: None)
    monkeypatch.setattr(listener, "_unregister_hotkeys", lambda: None)
    monkeypatch.setattr(listener, "SESSION_TYPE", "x11")
    monkeypatch.setattr(listener, "_config_watch_thread", type("T", (), {"is_alive": lambda self: True})())

    def fake_register(handlers):
        captured["actions"] = list(handlers)
        return object()

    monkeypatch.setattr(listener, "_register_x11_hotkeys", fake_register)

    listener.register_hotkeys_from_config()

    assert captured["actions"] == [
        "grammar",
        "prompt",
        "summarize",
        "explain",
        "tone",
        "capture_note",
        "open_chat",
        "ask_chat",
    ]


def test_process_selection_uses_requested_default_mode(fresh_modules, monkeypatch):
    listener = fresh_modules("listener")
    captured: dict[str, str] = {}

    monkeypatch.setattr(listener, "clipboard_capture", lambda: "hello world")
    monkeypatch.setattr(listener, "parse_mode_and_text", lambda text: ("grammar", text))
    monkeypatch.setattr(listener, "_write_temp_input", lambda body: "/tmp/in.txt")
    monkeypatch.setattr(listener, "_create_temp_output", lambda: "/tmp/out.txt")
    monkeypatch.setattr(listener, "_cleanup_temp", lambda *paths: None)
    monkeypatch.setattr(listener, "paste_back", lambda text: captured.setdefault("pasted", text))
    monkeypatch.setattr(listener, "_notify_mode_complete", lambda mode: captured.setdefault("notified", mode))

    def fake_run(mode: str, infile: str, outfile: str) -> str | None:
        captured["mode"] = mode
        captured["infile"] = infile
        captured["outfile"] = outfile
        return "processed text"

    monkeypatch.setattr(listener, "_run_mode_transform_subprocess", fake_run)

    listener.process_selection(default_mode="prompt")

    assert captured["mode"] == "prompt"
    assert captured["pasted"] == "processed text"
    assert captured["notified"] == "prompt"
