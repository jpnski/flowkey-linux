from __future__ import annotations

import pytest

pytest.importorskip("textual")


def test_ffchat_help_shows_usage(capsys):
    import ffchat

    rc = ffchat.main(["--help"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "ffchat" in out
    assert "--parent-pid" in out


def test_ffchat_version_prints(capsys):
    import ffchat
    import version

    rc = ffchat.main(["-V"])

    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert out == version.APP_VERSION


def test_ffchat_no_args_launches_tui(monkeypatch):
    import ffchat

    called = []

    def fake_tui(argv=None):
        called.append(argv)
        return 0

    monkeypatch.setattr(ffchat.tui_main, fake_tui)

    rc = ffchat.main([])

    assert rc == 0
    assert called == [None]


def test_ffchat_unknown_flag_passes_through(monkeypatch):
    import ffchat

    called = []

    def fake_tui(argv):
        called.append(argv)
        return 2

    monkeypatch.setattr(ffchat.tui_main, fake_tui)

    rc = ffchat.main(["--bogus"])

    assert rc == 2
    assert called == [["--bogus"]]
