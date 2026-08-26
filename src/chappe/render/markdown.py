"""Chatverlauf als Markdown oder Klartext."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .. import query
from ..model import media_class, ms_to_local, safe_filename

KIND_MARK = {
    "call": "☎",
    "update": "·",
    "deleted": "🗑",
    "sticker": "🎨",
    "viewOnce": "👁",
}


def _attachment_line(att: sqlite3.Row) -> str:
    cls = media_class(att["content_type"], att["flag"])
    label = {
        "voice": "Sprachnachricht",
        "image": "Bild",
        "gif": "GIF",
        "video": "Video",
        "audio": "Audio",
        "pdf": "PDF",
        "text": "Textdatei",
        "file": "Datei",
    }.get(cls, "Anhang")
    name = att["file_name"] or ""
    size = f"{att['size'] / 1024:.0f} KB" if att["size"] else ""
    missing = "" if att["local_path"] else " — nicht im Backup enthalten"
    detail = ", ".join(x for x in (name, size) if x)
    return f"[{label}{': ' + detail if detail else ''}{missing}]"


def render_transcript(
    conn: sqlite3.Connection,
    *,
    plain: bool = False,
    **filters,
) -> str:
    rows = query.transcript(conn, **filters)
    ids = [r["id"] for r in rows]
    atts = query.attachments_for(conn, ids)
    reas = query.reactions_for(conn, ids)

    out: list[str] = []
    last_day = None
    chat_name = rows[0]["chat"] if rows else "—"

    if not plain:
        out.append(f"# {chat_name}\n")
        if rows:
            first = ms_to_local(rows[0]["sent_at"])
            last = ms_to_local(rows[-1]["sent_at"])
            out.append(
                f"_{len(rows)} Nachrichten, "
                f"{first:%d.%m.%Y} bis {last:%d.%m.%Y}_\n"
            )

    for row in rows:
        stamp = ms_to_local(row["sent_at"])
        day = stamp.date() if stamp else None
        if day != last_day:
            last_day = day
            heading = f"{day:%A, %d. %B %Y}" if day else "Unbekanntes Datum"
            out.append(f"\n## {heading}\n" if not plain else f"\n--- {heading} ---\n")

        time = f"{stamp:%H:%M}" if stamp else "??:??"
        mark = KIND_MARK.get(row["kind"], "")
        author = row["author"]
        body = (row["body"] or "").strip()

        if row["kind"] in ("update", "call"):
            out.append(f"{time}  {mark} {body} ({author})")
            continue

        prefix = f"{time}  {author}:"
        lines = []
        if row["quote_text"]:
            quoted = " ".join(row["quote_text"].split())
            quoted = quoted if len(quoted) <= 140 else quoted[:139] + "…"
            lines.append(f"> {row['quote_author']}: {quoted}")
        for att in atts.get(row["id"], []):
            if att["role"] == "body":
                lines.append(_attachment_line(att))
        if body:
            lines.append(body)
        if row["is_edited"]:
            lines.append("_(bearbeitet)_" if not plain else "(bearbeitet)")
        emojis = "".join(r["emoji"] for r in reas.get(row["id"], []))
        if emojis:
            lines.append(f"↩ {emojis}")

        text = "\n".join(lines) if lines else "(leer)"
        indented = text.replace("\n", "\n      ")
        out.append(f"{prefix} {indented}")

    return "\n".join(out) + "\n"


def write_markdown(
    conn: sqlite3.Connection, out_dir: str | Path, *, plain: bool = False, **filters
) -> list[Path]:
    """Eine Datei je Chat."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ext = ".txt" if plain else ".md"
    written = []
    # Ein bereits gesetzter chat-Filter darf hier nicht verlorengehen — sonst würde
    # jede Iteration ihn mit dem jeweiligen Chatnamen überschreiben und es entstünden
    # Dateien für alle Chats statt nur für den gewünschten.
    chat_filter = (filters.get("chat") or "").lower()
    for chat in query.list_chats(conn, backup=filters.get("backup")):
        if chat_filter and chat_filter not in (chat["chat"] or "").lower():
            continue
        sub = dict(filters)
        sub["chat"] = chat["chat"]
        text = render_transcript(conn, plain=plain, **sub)
        path = out / (safe_filename(chat["chat"], 60) + ext)
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written
