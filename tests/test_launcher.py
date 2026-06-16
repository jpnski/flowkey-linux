from __future__ import annotations


def test_ffchat_tui_argv_uses_configured_terminal(monkeypatch):
    import launcher

    def fake_which(name: str):
        mapping = {
            "ffchat": "/usr/bin/ffchat",
            "kitty": "/usr/bin/kitty",
        }
        return mapping.get(name)

    monkeypatch.setattr(launcher.shutil, "which", fake_which)

    argv = launcher.ffchat_tui_argv("")

    assert argv == ["/usr/bin/kitty", "--", "/usr/bin/ffchat"]


def test_ffchat_tui_argv_uses_explicit_terminal(monkeypatch):
    import launcher

    def fake_which(name: str):
        return {"ffchat": "/usr/bin/ffchat"}.get(name)

    monkeypatch.setattr(launcher.shutil, "which", fake_which)

    argv = launcher.ffchat_tui_argv("alacritty --class ffchat")

    assert argv == ["alacritty", "--class", "ffchat", "-e", "/usr/bin/ffchat"]


def test_ffchat_tui_argv_returns_none_without_terminal(monkeypatch):
    import launcher

    monkeypatch.setattr(launcher.shutil, "which", lambda name: "/usr/bin/ffchat" if name == "ffchat" else None)

    assert launcher.ffchat_tui_argv("") is None
