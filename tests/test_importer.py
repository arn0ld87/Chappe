"""Tests für chappe.importer — Import des Mini-Backups aus der Fixture."""

from __future__ import annotations

import pytest

from chappe import importer


def test_report_counts(db):
    _, report = db
    assert report.recipients == 5
    assert report.chats == 3
    assert report.messages == 14
    assert report.revisions == 1
    assert report.attachments == 3
    assert report.attachments_local == 2
    assert report.reactions == 2
    assert report.quotes == 1
    assert report.quotes_resolved == 1
    assert report.calls == 2
    assert report.media_files == 3
    assert report.media_bound == 2
    assert report.media_orphans == 1


def test_message_direction_and_kind(db):
    conn, _ = db
    rows = {
        r["body"]: r
        for r in conn.execute(
            "SELECT body, direction, kind, subkind FROM messages WHERE revision_of IS NULL"
        ).fetchall()
    }
    assert rows["Hallo, wie geht es dir heute?"]["direction"] == "incoming"
    assert rows["Hallo, wie geht es dir heute?"]["kind"] == "standard"
    assert rows["Mir geht es gut, danke!"]["direction"] == "outgoing"
    assert rows["Notiz an mich selbst: Termin verschieben"]["direction"] == "directionless"
    assert rows["Diese Nachricht wurde gelöscht"]["kind"] == "deleted"
    assert rows["ist Signal beigetreten"]["kind"] == "update"
    assert rows["ist Signal beigetreten"]["subkind"] == "JOINED_SIGNAL"


def test_calls_have_correct_kind_and_subkind(db):
    conn, _ = db
    calls = conn.execute(
        "SELECT subkind, direction FROM messages WHERE kind = 'call' ORDER BY sent_at"
    ).fetchall()
    assert [c["subkind"] for c in calls] == ["AUDIO_CALL", "VIDEO_CALL"]
    # Anrufe kommen über eine incoming-Hülle herein.
    assert all(c["direction"] == "incoming" for c in calls)


def test_revision_is_separate_row_and_not_counted_as_message(db):
    conn, _ = db
    current = conn.execute(
        "SELECT id, is_edited, revision_of FROM messages "
        "WHERE body = 'Aktueller Text (bearbeitet)'"
    ).fetchone()
    assert current is not None
    assert current["is_edited"] == 1
    assert current["revision_of"] is None

    old = conn.execute(
        "SELECT revision_of, revision_index FROM messages WHERE body = 'Ursprünglicher Text'"
    ).fetchone()
    assert old is not None
    assert old["revision_of"] == current["id"]
    assert old["revision_index"] == 0

    # Die alte Fassung darf nicht als eigenständige Nachricht mitgezählt worden sein.
    n_current_body = conn.execute(
        "SELECT COUNT(*) AS n FROM messages "
        "WHERE body = 'Ursprünglicher Text' AND revision_of IS NULL"
    ).fetchone()["n"]
    assert n_current_body == 0


def test_quote_resolves_to_target_message(db):
    conn, _ = db
    target_id = conn.execute(
        "SELECT id FROM messages WHERE body = 'Hallo, wie geht es dir heute?'"
    ).fetchone()["id"]
    quote = conn.execute(
        """SELECT q.target_message_id, q.text
           FROM quotes q JOIN messages m ON m.id = q.message_id
           WHERE m.body LIKE 'Wie du weißt%'"""
    ).fetchone()
    assert quote["target_message_id"] == target_id
    assert quote["text"] == "Hallo, wie geht es dir heute?"


def test_attachment_local_path_only_when_file_present(db):
    conn, _ = db
    present = conn.execute(
        "SELECT local_path FROM attachments WHERE file_name = 'urlaub.png'"
    ).fetchone()
    assert present["local_path"] is not None

    missing = conn.execute(
        "SELECT local_path FROM attachments WHERE file_name = 'verlorenes_foto.jpg'"
    ).fetchone()
    assert missing["local_path"] is None


def test_default_import_strips_secrets(db):
    conn, _ = db
    for table in ("recipients", "messages"):
        raws = conn.execute(f"SELECT raw FROM {table}").fetchall()
        blob = "\n".join(r["raw"] for r in raws)
        for secret in ("svrPin", "profileKey", "identityKey"):
            assert secret not in blob, f"{secret} durfte nicht in {table}.raw stehen"


def test_keep_secrets_preserves_them(fixture_backup, tmp_path):
    conn = importer.connect(tmp_path / "vault_secrets.db")
    importer.import_backup(conn, fixture_backup, label="secrets", keep_secrets=True)
    # Nicht jedes Geheimnis steckt in jeder Tabelle (svrPin z. B. nur bei einem
    # recipient) — daher über beide Tabellen zusammen prüfen.
    blob = "\n".join(
        r["raw"]
        for table in ("recipients", "messages")
        for r in conn.execute(f"SELECT raw FROM {table}").fetchall()
    )
    for secret in ("svrPin", "profileKey", "identityKey"):
        assert secret in blob, f"{secret} sollte mit keep_secrets=True erhalten bleiben"


def test_reimport_without_replace_raises(fixture_backup, tmp_path):
    conn = importer.connect(tmp_path / "vault_dup.db")
    importer.import_backup(conn, fixture_backup, label="dup")
    with pytest.raises(ValueError):
        importer.import_backup(conn, fixture_backup, label="dup")


def test_reimport_with_replace_has_no_duplicates(fixture_backup, tmp_path):
    conn = importer.connect(tmp_path / "vault_replace.db")
    importer.import_backup(conn, fixture_backup, label="dup2")
    n_before = conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"]

    importer.import_backup(conn, fixture_backup, label="dup2", replace=True)
    n_after = conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"]

    assert n_before == n_after
    # Auch bei Empfängern und Chats keine Dubletten.
    assert conn.execute("SELECT COUNT(*) AS n FROM recipients").fetchone()["n"] == 5
    assert conn.execute("SELECT COUNT(*) AS n FROM chats").fetchone()["n"] == 3
