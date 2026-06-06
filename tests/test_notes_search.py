from __future__ import annotations

import notes


def test_search_notes_nested_category(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    note_dir = vault / "work" / "technical"
    note_dir.mkdir(parents=True)
    (note_dir / "sample.md").write_text(
        "---\ntitle: Sample\n---\n\npython asyncio tutorial\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(notes, "_vault_dir", lambda: vault)
    out = notes.search_notes("python", limit=5)
    assert out["count"] == 1
    assert out["results"][0]["category"] == "work/technical"


def test_write_note_allows_inbox(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    monkeypatch.setattr(notes, "_vault_dir", lambda: vault)

    path = notes._write_note(
        notes.INBOX,
        "2026-01-01-1200",
        "sample",
        {"title": "Sample"},
        "Body\n",
    )

    assert path == vault / "inbox" / "2026-01-01-1200-sample.md"
    assert path.exists()
