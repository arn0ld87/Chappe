"""Tests für die Renderer — Transkripte, Dateinamen, Chatabgrenzung."""

from __future__ import annotations

from pathlib import Path

import pytest

from chappe import importer, query
from chappe.render.html import write_html
from chappe.render.markdown import render_transcript, write_markdown


@pytest.fixture
def db_two_backups(fixture_backup: Path, tmp_path: Path):
    """Dasselbe Backup zweimal unter verschiedenen Labels.

    Bildet die reale Lage nach: zwei Signal-Accounts, deren Kontakte sich
    überschneiden, liegen in einer Datenbank und liefern Chats mit identischem
    Namen.
    """
    conn = importer.connect(tmp_path / "zwei.db")
    importer.import_backup(conn, fixture_backup, label="eins")
    importer.import_backup(conn, fixture_backup, label="zwei")
    yield conn
    conn.close()


def test_fixture_yields_same_named_chats_in_both_backups(db_two_backups):
    """Vorbedingung des Regressionstests: gleicher Chatname, zwei Zeilen."""
    chats = query.list_chats(db_two_backups)
    names = [c["chat"] for c in chats]
    assert len(names) > len(set(names))                      # Name kommt doppelt vor
    assert len({c["chat_id"] for c in chats}) == len(chats)  # chat_id bleibt eindeutig


def test_transcript_by_name_mixes_backups_by_id_does_not(db_two_backups):
    """Der Grund, warum die Renderer über chat_id filtern müssen."""
    chats = query.list_chats(db_two_backups)
    name = chats[0]["chat"]
    by_name = query.transcript(db_two_backups, chat=name)
    per_id = [
        len(query.transcript(db_two_backups, chat_id=c["chat_id"]))
        for c in chats
        if c["chat"] == name
    ]
    assert len(per_id) == 2
    assert len(by_name) == sum(per_id)   # Namensfilter zieht beide Chats zusammen
    assert per_id[0] == per_id[1]        # jeder für sich bleibt vollständig


def test_write_markdown_keeps_same_named_chats_apart(db_two_backups, tmp_path):
    """Ein gleichnamiger Chat aus zwei Backups darf sich weder in einer Datei
    noch über einen kollidierenden Dateinamen vermischen."""
    chats = query.list_chats(db_two_backups)
    paths = write_markdown(db_two_backups, tmp_path / "md")

    assert len(paths) == len(chats)
    assert len({p.name for p in paths}) == len(paths)

    expected = sum(c["messages"] for c in chats)
    written = sum(
        len(query.transcript(db_two_backups, chat_id=c["chat_id"])) for c in chats
    )
    assert written == expected

    # Kein Transkript trägt mehr Nachrichtenzeilen als sein Chat hat.
    for chat, path in zip(sorted(chats, key=lambda c: -c["messages"]), paths):
        text = path.read_text(encoding="utf-8")
        assert f"{chat['messages']} Nachrichten" in text


def test_write_markdown_honours_chat_filter(db_two_backups, tmp_path):
    """Ein gesetzter chat-Filter darf in der Schleife nicht verlorengehen."""
    chats = query.list_chats(db_two_backups)
    name = chats[0]["chat"]
    paths = write_markdown(db_two_backups, tmp_path / "gefiltert", chat=name)
    matching = [c for c in chats if name.lower() in (c["chat"] or "").lower()]
    assert len(paths) == len(matching)


def test_write_markdown_single_backup_uses_plain_names(db, tmp_path):
    """Bei nur einem Backup bleibt der Dateiname der schlichte Chatname."""
    conn, _ = db
    paths = write_markdown(conn, tmp_path / "eins")
    assert paths
    assert all("__" not in p.name for p in paths)


def test_write_html_writes_one_file_per_chat_plus_index(db_two_backups, tmp_path):
    chats = query.list_chats(db_two_backups)
    paths = write_html(db_two_backups, tmp_path / "html", media="none")
    assert len({p.name for p in paths}) == len(paths)
    assert (tmp_path / "html" / "index.html").exists()
    assert len([p for p in paths if p.name != "index.html"]) == len(chats)


def test_render_transcript_plain_has_no_markdown_heading(db):
    conn, _ = db
    chat = query.list_chats(conn)[0]
    text = render_transcript(conn, plain=True, chat_id=chat["chat_id"])
    assert not text.startswith("#")
    assert "---" in text   # Tagestrenner in der Klartextfassung
