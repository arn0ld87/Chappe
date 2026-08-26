"""Abfragen: Suche, Verlauf, Auswertungen."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta

# Nachrichtenarten, die in Auswertungen als "echte Nachricht" zählen.
CONTENT_KINDS = ("standard", "sticker", "viewOnce", "payment", "giftBadge", "storyReply")


def parse_date(value: str | None, end: bool = False) -> int | None:
    """'2026-03', '2026-03-14', '2026-03-14 18:00' -> ms seit Epoch."""
    if not value:
        return None
    value = value.strip()
    formats = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y-%m", "%Y")
    for fmt in formats:
        try:
            dt = datetime.strptime(value, fmt)
        except ValueError:
            continue
        if end:
            if fmt == "%Y":
                dt = dt.replace(year=dt.year + 1)
            elif fmt == "%Y-%m":
                dt = dt.replace(year=dt.year + (dt.month // 12), month=dt.month % 12 + 1)
            elif fmt == "%Y-%m-%d":
                dt = dt + timedelta(days=1)
            elif fmt == "%Y-%m-%d %H:%M":
                dt = dt + timedelta(minutes=1)
            else:
                dt = dt + timedelta(seconds=1)
        return int(dt.astimezone().timestamp() * 1000)
    raise ValueError(f"Datum nicht verstanden: {value!r} (erwartet z. B. 2026-03-14)")


def resolve_chat_ids(
    conn: sqlite3.Connection, chat: str, backup: str | None = None
) -> list[int]:
    """Chatbezeichnung -> Zeilen-IDs.

    Ein exakt passender Name gewinnt gegen jede Teiltreffer-Suche. Sonst würde
    ein Chat „Alex" auch „Alexander Schneider" und „Alexander Zietlow" einsammeln
    und deren Nachrichten in einer Auswertung vermischen.
    """
    sql = "SELECT c.id FROM chats c JOIN backups b ON b.id = c.backup_id WHERE "
    scope = " AND b.label = ?" if backup else ""

    exact = conn.execute(
        sql + "c.name = ?" + scope, ([chat, backup] if backup else [chat])
    ).fetchall()
    if exact:
        return [r["id"] for r in exact]

    like = conn.execute(
        sql + "c.name LIKE ?" + scope,
        ([f"%{chat}%", backup] if backup else [f"%{chat}%"]),
    ).fetchall()
    return [r["id"] for r in like]


def build_filter(conn: sqlite3.Connection, **filters) -> tuple[str, list]:
    """Öffentlicher Zugang zur Filterlogik — für eigene SQL-Abfragen."""
    return _filters(conn, **filters)


def _filters(
    conn: sqlite3.Connection,
    *,
    backup: str | None = None,
    chat: str | None = None,
    chat_id: int | None = None,
    author: str | None = None,
    since: str | None = None,
    until: str | None = None,
    kinds: tuple[str, ...] | None = None,
    with_media: bool = False,
) -> tuple[str, list]:
    sql, params = "", []
    if backup:
        sql += " AND b.label = ?"
        params.append(backup)
    if chat_id is not None:
        sql += " AND m.chat_id = ?"
        params.append(chat_id)
    elif chat:
        # Über die aufgelösten IDs filtern statt über den Namen: eindeutig, und
        # ein Chatname, der Teilstring eines anderen ist, zieht nichts mit hinein.
        ids = resolve_chat_ids(conn, chat, backup)
        if not ids:
            sql += " AND 0"          # nichts gefunden -> leeres Ergebnis
        else:
            sql += f" AND m.chat_id IN ({','.join('?' * len(ids))})"
            params.extend(ids)
    if author:
        sql += " AND r.display_name LIKE ?"
        params.append(f"%{author}%")
    lo, hi = parse_date(since), parse_date(until, end=True)
    if lo:
        sql += " AND m.sent_at >= ?"
        params.append(lo)
    if hi:
        sql += " AND m.sent_at < ?"
        params.append(hi)
    if kinds:
        sql += f" AND m.kind IN ({','.join('?' * len(kinds))})"
        params.extend(kinds)
    if with_media:
        sql += " AND m.n_attachments > 0"
    return sql, params


BASE = """
    FROM messages m
    JOIN chats   c ON c.id = m.chat_id
    JOIN backups b ON b.id = m.backup_id
    LEFT JOIN recipients r ON r.id = m.author_id
    WHERE m.revision_of IS NULL
"""


def list_chats(conn: sqlite3.Connection, backup: str | None = None) -> list[sqlite3.Row]:
    sql = "SELECT * FROM v_chat_overview WHERE messages > 0"
    params: list = []
    if backup:
        sql += " AND backup = ?"
        params.append(backup)
    sql += " ORDER BY messages DESC"
    return conn.execute(sql, params).fetchall()


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = 50,
    **filters,
) -> list[sqlite3.Row]:
    """Volltextsuche über FTS5. Unterstützt Phrasen ("…"), OR, NEAR, Präfix (foo*)."""
    where, params = _filters(conn, **filters)
    sql = f"""
        SELECT m.id, b.label AS backup, c.name AS chat,
               COALESCE(r.display_name, '?') AS author, m.direction, m.kind,
               m.sent_at, m.n_attachments, m.n_reactions,
               snippet(messages_fts, 0, '‹', '›', '…', 14) AS snippet,
               m.body
        FROM messages_fts f
        JOIN messages m ON m.id = f.rowid
        JOIN chats   c ON c.id = m.chat_id
        JOIN backups b ON b.id = m.backup_id
        LEFT JOIN recipients r ON r.id = m.author_id
        WHERE messages_fts MATCH ? AND m.revision_of IS NULL {where}
        ORDER BY rank
        LIMIT ?
    """
    return conn.execute(sql, [query, *params, limit]).fetchall()


def search_like(
    conn: sqlite3.Connection, needle: str, *, limit: int = 50, **filters
) -> list[sqlite3.Row]:
    """Wörtliche Teilstringsuche — findet auch, was der Tokenizer zerlegt."""
    where, params = _filters(conn, **filters)
    sql = f"""
        SELECT m.id, b.label AS backup, c.name AS chat,
               COALESCE(r.display_name, '?') AS author, m.direction, m.kind,
               m.sent_at, m.n_attachments, m.n_reactions, m.body,
               m.body AS snippet
        {BASE} AND m.body LIKE ? {where}
        ORDER BY m.sent_at DESC LIMIT ?
    """
    return conn.execute(sql, [f"%{needle}%", *params, limit]).fetchall()


def transcript(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
    offset: int = 0,
    ascending: bool = True,
    **filters,
) -> list[sqlite3.Row]:
    """Nachrichtenverlauf mit Zitat, Reaktionen und Anhängen."""
    where, params = _filters(conn, **filters)
    sql = f"""
        SELECT m.id, b.label AS backup, c.name AS chat, c.id AS chat_id,
               COALESCE(r.display_name, '?') AS author, m.author_id,
               m.direction, m.kind, m.subkind, m.sent_at, m.body,
               m.n_attachments, m.n_reactions, m.has_quote, m.is_edited,
               q.text AS quote_text, q.target_message_id AS quote_target,
               COALESCE(qr.display_name, '') AS quote_author,
               (SELECT group_concat(emoji, '') FROM reactions x
                 WHERE x.message_id = m.id) AS reaction_emojis
        {BASE} {where}
        LEFT JOIN quotes q ON q.message_id = m.id
        LEFT JOIN recipients qr ON qr.id = q.author_id
        ORDER BY m.sent_at {'ASC' if ascending else 'DESC'}, m.id
    """
    # LEFT JOINs müssen vor WHERE stehen — daher neu zusammensetzen:
    sql = f"""
        SELECT m.id, b.label AS backup, c.name AS chat, c.id AS chat_id,
               COALESCE(r.display_name, '?') AS author, m.author_id,
               m.direction, m.kind, m.subkind, m.sent_at, m.body,
               m.n_attachments, m.n_reactions, m.has_quote, m.is_edited,
               q.text AS quote_text, q.target_message_id AS quote_target,
               COALESCE(qr.display_name, '') AS quote_author,
               (SELECT group_concat(emoji, '') FROM reactions x
                 WHERE x.message_id = m.id) AS reaction_emojis
        FROM messages m
        JOIN chats   c ON c.id = m.chat_id
        JOIN backups b ON b.id = m.backup_id
        LEFT JOIN recipients r ON r.id = m.author_id
        LEFT JOIN quotes q ON q.message_id = m.id
        LEFT JOIN recipients qr ON qr.id = q.author_id
        WHERE m.revision_of IS NULL {where}
        ORDER BY m.sent_at {'ASC' if ascending else 'DESC'}, m.id
    """
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params = [*params, limit, offset]
    return conn.execute(sql, params).fetchall()


def attachments_for(conn: sqlite3.Connection, message_ids: list[int]) -> dict[int, list]:
    if not message_ids:
        return {}
    out: dict[int, list] = {}
    chunk = 900
    for i in range(0, len(message_ids), chunk):
        part = message_ids[i : i + chunk]
        rows = conn.execute(
            f"""SELECT * FROM attachments
                WHERE message_id IN ({','.join('?' * len(part))})
                ORDER BY message_id, ordinal""",
            part,
        ).fetchall()
        for row in rows:
            out.setdefault(row["message_id"], []).append(row)
    return out


def reactions_for(conn: sqlite3.Connection, message_ids: list[int]) -> dict[int, list]:
    if not message_ids:
        return {}
    out: dict[int, list] = {}
    chunk = 900
    for i in range(0, len(message_ids), chunk):
        part = message_ids[i : i + chunk]
        rows = conn.execute(
            f"""SELECT x.message_id, x.emoji, COALESCE(r.display_name,'?') AS author
                FROM reactions x
                LEFT JOIN recipients r ON r.id = x.author_id
                WHERE x.message_id IN ({','.join('?' * len(part))})
                ORDER BY x.message_id, x.ordinal""",
            part,
        ).fetchall()
        for row in rows:
            out.setdefault(row["message_id"], []).append(row)
    return out


# ------------------------------------------------------------------ Auswertung

_WORD = re.compile(r"[\w'’-]+", re.UNICODE)


def stats(conn: sqlite3.Connection, **filters) -> dict:
    where, params = _filters(conn, **filters)
    content = f" AND m.kind IN ({','.join('?' * len(CONTENT_KINDS))})"
    cp = list(CONTENT_KINDS)

    def q(sql: str, extra: list | None = None):
        return conn.execute(sql, [*params, *cp, *(extra or [])]).fetchall()

    overall = q(
        f"""SELECT COUNT(*) AS n,
                   SUM(m.direction='outgoing') AS sent,
                   SUM(m.direction='incoming') AS received,
                   SUM(m.n_attachments) AS attachments,
                   MIN(m.sent_at) AS first, MAX(m.sent_at) AS last
            {BASE} {where} {content}"""
    )[0]

    by_author = q(
        f"""SELECT COALESCE(r.display_name,'?') AS author, COUNT(*) AS n,
                   SUM(LENGTH(COALESCE(m.body,''))) AS chars,
                   SUM(m.n_attachments) AS attachments
            {BASE} {where} {content}
            GROUP BY author ORDER BY n DESC"""
    )

    by_month = q(
        f"""SELECT strftime('%Y-%m', m.sent_at/1000, 'unixepoch', 'localtime') AS bucket,
                   COUNT(*) AS n,
                   SUM(m.direction='outgoing') AS sent,
                   SUM(m.direction='incoming') AS received
            {BASE} {where} {content}
            GROUP BY bucket ORDER BY bucket"""
    )

    by_hour = q(
        f"""SELECT CAST(strftime('%H', m.sent_at/1000,'unixepoch','localtime') AS INTEGER)
                   AS bucket, COUNT(*) AS n
            {BASE} {where} {content}
            GROUP BY bucket ORDER BY bucket"""
    )

    by_weekday = q(
        f"""SELECT CAST(strftime('%w', m.sent_at/1000,'unixepoch','localtime') AS INTEGER)
                   AS bucket, COUNT(*) AS n
            {BASE} {where} {content}
            GROUP BY bucket ORDER BY bucket"""
    )

    media = q(
        f"""SELECT a.content_type, COUNT(*) AS n, SUM(a.size) AS bytes,
                   SUM(a.local_path IS NOT NULL) AS local
            {BASE} {where} {content}
            AND m.id IN (SELECT message_id FROM attachments)
            GROUP BY a.content_type""".replace(
            "FROM messages m", "FROM attachments a JOIN messages m ON m.id = a.message_id"
        )
    )

    reactions = q(
        f"""SELECT x.emoji, COUNT(*) AS n
            {BASE} {where} {content}
            AND m.id = x.message_id
            GROUP BY x.emoji ORDER BY n DESC LIMIT 15""".replace(
            "FROM messages m", "FROM reactions x JOIN messages m ON m.id = x.message_id"
        )
    )

    # Anrufe brauchen die Inhaltsfilter nicht — eigener Aufruf ohne CONTENT_KINDS.
    calls = conn.execute(
        f"""SELECT cl.call_type, cl.direction, cl.state, COUNT(*) AS n
            FROM calls cl
            JOIN messages m ON m.id = cl.message_id
            JOIN chats   c ON c.id = m.chat_id
            JOIN backups b ON b.id = m.backup_id
            LEFT JOIN recipients r ON r.id = m.author_id
            WHERE m.revision_of IS NULL {where}
            GROUP BY cl.call_type, cl.direction, cl.state
            ORDER BY n DESC""",
        params,
    ).fetchall()

    return {
        "overall": overall,
        "by_author": by_author,
        "by_month": by_month,
        "by_hour": by_hour,
        "by_weekday": by_weekday,
        "media": media,
        "reactions": reactions,
        "calls": calls,
        "response_times": response_times(conn, **filters),
        "top_words": top_words(conn, **filters),
    }


def response_times(conn: sqlite3.Connection, top: int = 0, **filters) -> list[sqlite3.Row]:
    """Median-Antwortzeit je Person: Zeit bis zur ersten Antwort nach einem
    Sprecherwechsel, Pausen über 12 Stunden ausgenommen."""
    where, params = _filters(conn, **filters)
    rows = conn.execute(
        f"""SELECT m.chat_id, m.author_id, COALESCE(r.display_name,'?') AS author,
                   m.sent_at
            {BASE} {where} AND m.kind IN ('standard','sticker')
            ORDER BY m.chat_id, m.sent_at""",
        params,
    ).fetchall()

    gaps: dict[str, list[int]] = {}
    prev_chat = prev_author = None
    prev_time = 0
    for row in rows:
        if row["chat_id"] != prev_chat:
            prev_chat, prev_author, prev_time = row["chat_id"], row["author_id"], row["sent_at"]
            continue
        if row["author_id"] != prev_author:
            delta = row["sent_at"] - prev_time
            if 0 < delta <= 12 * 3600 * 1000:
                gaps.setdefault(row["author"], []).append(delta)
            prev_author = row["author_id"]
        prev_time = row["sent_at"]

    out = []
    for author, values in gaps.items():
        values.sort()
        mid = len(values) // 2
        median = values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) // 2
        out.append(
            {
                "author": author,
                "n": len(values),
                "median_s": median / 1000,
                "mean_s": sum(values) / len(values) / 1000,
            }
        )
    out.sort(key=lambda x: x["median_s"])
    return out


STOPWORDS = set(
    """der die das und ist ich du er sie es wir ihr den dem des ein eine einen einem einer
    nicht auch aber oder wenn dann noch nur schon mal ja nein so wie was wer wo warum
    hat habe haben hab bin bist sind war waren wird werden kann kannst können muss musst
    mit von zu zur zum für auf an in im am als bei aus nach über um vor durch dass da
    mich mir dich dir sich uns euch sein ihre ihr mein meine dein deine
    doch halt eben mehr sehr gut ganz immer heute jetzt hier dort etwas alles nichts
    the and you that for are was this have with not but
    ok okay ne nen na hm hmm ah oh""".split()
)


def top_words(conn: sqlite3.Connection, limit: int = 25, min_len: int = 4, **filters):
    where, params = _filters(conn, **filters)
    rows = conn.execute(
        f"""SELECT COALESCE(r.display_name,'?') AS author, m.body
            {BASE} {where} AND m.kind = 'standard' AND m.body IS NOT NULL""",
        params,
    ).fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        for word in _WORD.findall(row["body"].lower()):
            if len(word) < min_len or word in STOPWORDS or word.isdigit():
                continue
            counts[word] = counts.get(word, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])[:limit]


def timeline(conn: sqlite3.Connection, granularity: str = "month", **filters):
    fmt = {"day": "%Y-%m-%d", "week": "%Y-W%W", "month": "%Y-%m", "year": "%Y"}[granularity]
    where, params = _filters(conn, **filters)
    return conn.execute(
        f"""SELECT strftime('{fmt}', m.sent_at/1000,'unixepoch','localtime') AS bucket,
                   COUNT(*) AS n,
                   SUM(m.direction='outgoing') AS sent,
                   SUM(m.direction='incoming') AS received,
                   SUM(m.n_attachments) AS attachments
            {BASE} {where} AND m.kind IN ('standard','sticker')
            GROUP BY bucket ORDER BY bucket""",
        params,
    ).fetchall()
