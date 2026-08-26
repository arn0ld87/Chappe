"""Anhänge aus dem Backup mit sprechenden Namen bereitstellen."""

from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .model import extension_for, media_class, ms_to_local, safe_filename
from .query import resolve_chat_ids


@dataclass
class MediaExportResult:
    exported: int = 0
    reused: int = 0
    missing: int = 0
    bytes_written: int = 0


def export_name_for(row: sqlite3.Row) -> str:
    """<Datum>_<Uhrzeit>_<Autor>_<Original-/Klassenname>.<ext>"""
    stamp = ms_to_local(row["sent_at"])
    prefix = stamp.strftime("%Y-%m-%d_%H%M%S") if stamp else "unbekannt"
    author = safe_filename(row["author"] or "unbekannt", 24)
    ext = extension_for(row["content_type"], row["file_name"])
    if row["file_name"]:
        stem = safe_filename(row["file_name"].rsplit(".", 1)[0], 40)
    else:
        stem = media_class(row["content_type"], row["flag"])
    return f"{prefix}_{author}_{stem}_{row['id']}{ext}"


def media_rows(
    conn: sqlite3.Connection,
    *,
    backup: str | None = None,
    chat: str | None = None,
    media_type: str | None = None,
    only_local: bool = True,
) -> list[sqlite3.Row]:
    sql = """
        SELECT a.id, a.message_id, a.role, a.content_type, a.file_name, a.flag,
               a.size, a.local_path, a.plaintext_hash, a.export_name,
               m.sent_at, c.name AS chat, b.label AS backup,
               COALESCE(r.display_name, '?') AS author
        FROM attachments a
        JOIN messages m ON m.id = a.message_id
        JOIN chats    c ON c.id = m.chat_id
        JOIN backups  b ON b.id = m.backup_id
        LEFT JOIN recipients r ON r.id = m.author_id
        WHERE 1 = 1
    """
    params: list = []
    if only_local:
        sql += " AND a.local_path IS NOT NULL"
    if backup:
        sql += " AND b.label = ?"
        params.append(backup)
    if chat:
        # Wie in query._filters: über die aufgelösten Chat-IDs filtern, damit ein
        # Chatname, der Teilstring eines anderen ist, keine fremden Anhänge mitnimmt.
        ids = resolve_chat_ids(conn, chat, backup)
        if not ids:
            return []
        sql += f" AND m.chat_id IN ({','.join('?' * len(ids))})"
        params.extend(ids)
    if media_type:
        sql += " AND a.content_type LIKE ?"
        params.append(f"{media_type}%")
    sql += " ORDER BY m.sent_at, a.ordinal"
    return conn.execute(sql, params).fetchall()


def source_path(conn: sqlite3.Connection, row: sqlite3.Row) -> Path | None:
    """Absoluter Pfad der Datei im Backup-Verzeichnis."""
    if not row["local_path"]:
        return None
    base = conn.execute(
        "SELECT source_path FROM backups WHERE label = ?", (row["backup"],)
    ).fetchone()
    if not base:
        return None
    # `local_path` ist bereits relativ zum Backup-Verzeichnis (also "files/…"),
    # und `backups.source_path` IST dieses Verzeichnis — hier darf kein .parent stehen.
    path = Path(base["source_path"]) / row["local_path"]
    return path if path.exists() else None


def export_media(
    conn: sqlite3.Connection,
    out_dir: str | os.PathLike,
    *,
    backup: str | None = None,
    chat: str | None = None,
    media_type: str | None = None,
    mode: str = "link",  # link | copy
    group_by_chat: bool = True,
    progress=None,
) -> MediaExportResult:
    """Legt alle lokal vorhandenen Anhänge unter sprechenden Namen ab.

    `link` erzeugt Hardlinks (fällt auf Kopie zurück, wenn das Dateisystem nicht
    mitspielt) und kostet daher keinen zusätzlichen Speicher.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    result = MediaExportResult()
    rows = media_rows(conn, backup=backup, chat=chat, media_type=media_type)
    progress = progress or (lambda _m: None)

    for n, row in enumerate(rows, 1):
        src = source_path(conn, row)
        if src is None:
            result.missing += 1
            continue
        target_dir = out / safe_filename(row["chat"], 60) if group_by_chat else out
        target_dir.mkdir(parents=True, exist_ok=True)
        name = export_name_for(row)
        dst = target_dir / name

        if dst.exists() and dst.stat().st_size == src.stat().st_size:
            result.reused += 1
        else:
            if mode == "link":
                try:
                    if dst.exists():
                        dst.unlink()
                    os.link(src, dst)
                except OSError:
                    shutil.copy2(src, dst)
            else:
                shutil.copy2(src, dst)
            result.exported += 1
            result.bytes_written += src.stat().st_size

        conn.execute(
            "UPDATE attachments SET export_name = ? WHERE id = ?",
            (str(dst.relative_to(out)), row["id"]),
        )
        if n % 100 == 0:
            progress(f"  {n}/{len(rows)} Anhänge …")

    conn.commit()
    return result
