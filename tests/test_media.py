"""Tests für chappe.media — Medien-Export mit sprechenden Namen."""

from __future__ import annotations

from make_fixture import build_fixture  # von conftest.py auf sys.path gelegt

from chappe import importer, media


def test_export_media_copy_creates_named_files_and_sets_export_name(db, tmp_path):
    conn, _ = db
    out_dir = tmp_path / "export"

    local_rows_before = media.media_rows(conn, only_local=True)
    assert local_rows_before, "Fixture sollte mindestens einen lokal vorhandenen Anhang haben"

    result = media.export_media(conn, out_dir, mode="copy")

    assert result.exported == len(local_rows_before)
    assert result.missing == 0

    exported = conn.execute(
        "SELECT export_name FROM attachments WHERE export_name IS NOT NULL"
    ).fetchall()
    assert len(exported) == len(local_rows_before)
    for row in exported:
        assert row["export_name"]
        assert (out_dir / row["export_name"]).exists()
        # Kopiermodus: es entstehen echte Dateien, keine Links.
        assert (out_dir / row["export_name"]).is_file()


def test_export_media_second_run_counts_as_reused(db, tmp_path):
    conn, _ = db
    out_dir = tmp_path / "export"

    first = media.export_media(conn, out_dir, mode="copy")
    second = media.export_media(conn, out_dir, mode="copy")

    assert first.exported > 0
    assert second.exported == 0
    assert second.reused == first.exported


def test_export_media_counts_missing_when_file_disappears(tmp_path):
    # Eigene, private Kopie des Backups — hier wird bewusst eine Datei
    # gelöscht, das darf die geteilte Session-Fixture nicht beeinflussen.
    backup_dir = build_fixture(tmp_path / "backup")
    conn = importer.connect(tmp_path / "vault.db")
    importer.import_backup(conn, backup_dir, label="media-missing")

    row = conn.execute(
        "SELECT local_path FROM attachments WHERE file_name = 'urlaub.png'"
    ).fetchone()
    assert row["local_path"] is not None
    (backup_dir / row["local_path"]).unlink()

    out_dir = tmp_path / "export"
    result = media.export_media(conn, out_dir, mode="copy")

    assert result.missing == 1
    assert result.exported == 1  # nur die zweite lokale Datei (Linkvorschau) bleibt übrig


def test_export_name_for_has_no_path_separators(db):
    conn, _ = db
    rows = media.media_rows(conn, only_local=True)
    assert rows
    for row in rows:
        name = media.export_name_for(row)
        assert "/" not in name
        assert "\\" not in name
        assert name  # nicht leer
