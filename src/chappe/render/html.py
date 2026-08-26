"""HTML-Export: eigenständige Chat-Seiten mit Bubbles, Suche und Statistik.

Erzeugt eine `index.html` mit Übersicht sowie je Chat eine eigenständige
HTML-Datei (CSS/JS inline, keine externen Ressourcen). Medien werden über
`media.export_media` bereitgestellt und relativ unter `media/` verlinkt.

Liegen mehrere Backups (Accounts) in derselben Datenbank, gruppiert die
Übersichtsseite die Chats nach Backup, und die Dateinamen der Chat-Seiten
bekommen ein `<backup-label>__` Präfix, damit gleichnamige Chats
verschiedener Accounts sich nicht überschreiben.
"""

from __future__ import annotations

import html
import re
import sqlite3
from pathlib import Path
from urllib.parse import quote

from .. import query
from ..media import export_media
from ..model import media_class, ms_to_local, safe_filename

# --------------------------------------------------------------------- Text

WEEKDAYS = [
    "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag",
]
MONTHS = [
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]

_MEDIA_LABEL = {
    "voice": "Sprachnachricht",
    "image": "Bild",
    "gif": "GIF",
    "video": "Video",
    "audio": "Audio",
    "pdf": "PDF",
    "text": "Textdatei",
    "file": "Datei",
}

_URL_RE = re.compile(r'(https?://[^\s<>"\']+)')


def _fmt_date_heading(dt) -> str:
    return f"{WEEKDAYS[dt.weekday()]}, {dt.day}. {MONTHS[dt.month - 1]} {dt.year}"


def _fmt_bytes(n: int | None) -> str:
    if not n:
        return ""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "–"
    total = int(round(seconds))
    if total < 60:
        return f"{total} s"
    minutes, sec = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} min {sec} s" if sec else f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


def _fmt_period_ms(first_ms: int | None, last_ms: int | None) -> str:
    first, last = ms_to_local(first_ms), ms_to_local(last_ms)
    if not first or not last:
        return ""
    if first.date() == last.date():
        return f"{first:%d.%m.%Y}"
    return f"{first:%d.%m.%Y} – {last:%d.%m.%Y}"


def _linkify(escaped_text: str) -> str:
    def repl(m: re.Match) -> str:
        url = m.group(1)
        trail = ""
        while url and url[-1] in ".,;:!?)":
            trail = url[-1] + trail
            url = url[:-1]
        if not url:
            return m.group(1)
        return f'<a href="{url}" target="_blank" rel="noopener noreferrer">{url}</a>{trail}'

    return _URL_RE.sub(repl, escaped_text)


def _format_body(text: str) -> str:
    """Escapt, verlinkt URLs und wandelt Zeilenumbrüche in <br> um."""
    escaped = html.escape(text)
    linked = _linkify(escaped)
    return linked.replace("\n", "<br>")


# ------------------------------------------------------------------ Bausteine


def _quote_html(row: sqlite3.Row) -> str:
    if not row["quote_text"]:
        return ""
    text = " ".join(row["quote_text"].split())
    if len(text) > 200:
        text = text[:199] + "…"
    text = html.escape(text)
    author = html.escape(row["quote_author"] or "")
    inner = f'<span class="quote-author">{author}</span> {text}' if author else text
    if row["quote_target"]:
        return f'<a class="quote" href="#m{row["quote_target"]}">{inner}</a>'
    return f'<div class="quote">{inner}</div>'


def _reactions_html(reas: list[sqlite3.Row]) -> str:
    if not reas:
        return ""
    groups: dict[str, list[str]] = {}
    order: list[str] = []
    for r in reas:
        emoji = r["emoji"]
        if emoji not in groups:
            groups[emoji] = []
            order.append(emoji)
        groups[emoji].append(r["author"] or "?")
    chips = []
    for emoji in order:
        names = groups[emoji]
        title = html.escape(", ".join(names))
        chips.append(f'<span class="reaction" title="{title}">{html.escape(emoji)} {len(names)}</span>')
    return f'<div class="reactions">{"".join(chips)}</div>'


def _attachment_html(att: sqlite3.Row) -> str:
    cls = media_class(att["content_type"], att["flag"])
    name = html.escape(att["file_name"] or "")
    size_txt = _fmt_bytes(att["size"])
    export_name = att["export_name"]

    if export_name:
        src = html.escape("media/" + quote(str(export_name)))
        alt = name or _MEDIA_LABEL.get(cls, "Anhang")
        if cls in ("image", "gif"):
            return (
                f'<span class="att-image" onclick="svLightbox(this)">'
                f'<img src="{src}" loading="lazy" alt="{alt}"></span>'
            )
        if cls == "video":
            return f'<video controls preload="metadata" src="{src}"></video>'
        if cls in ("voice", "audio"):
            mic = '<span class="mic" title="Sprachnachricht">🎤</span>' if cls == "voice" else ""
            return f'<div class="att-audio">{mic}<audio controls src="{src}"></audio></div>'
        detail = f" ({size_txt})" if size_txt else ""
        return (
            f'<a class="att-file" href="{src}" target="_blank" '
            f'rel="noopener noreferrer">{alt}{detail}</a>'
        )

    label = name or _MEDIA_LABEL.get(cls, "Anhang")
    reason = "nicht im Backup enthalten" if not att["local_path"] else "nicht exportiert"
    bits = [b for b in (label, size_txt, reason) if b]
    return f'<div class="att-missing">{" · ".join(bits)}</div>'


def _message_html(row: sqlite3.Row, atts: list[sqlite3.Row], reas: list[sqlite3.Row], show_author: bool) -> str:
    mid = row["id"]
    stamp = ms_to_local(row["sent_at"])
    time_txt = f"{stamp:%H:%M}" if stamp else "--:--"
    kind = row["kind"]
    body = (row["body"] or "").strip()

    if kind in ("update", "call"):
        search_attr = html.escape(body.lower())
        return (
            f'<div class="sys" id="m{mid}" data-search="{search_attr}">'
            f'<span class="sys-time">{time_txt}</span> {html.escape(body)}</div>'
        )

    side = "out" if row["direction"] == "outgoing" else "in"
    classes = f"msg {side}"

    author_html = f'<div class="author">{html.escape(row["author"] or "")}</div>' if show_author else ""
    quote_html = _quote_html(row)

    if kind == "deleted":
        text = html.escape(body) if body else "Diese Nachricht wurde gelöscht"
        body_html = f'<div class="body deleted-text">{text}</div>'
    elif body:
        body_html = f'<div class="body">{_format_body(body)}</div>'
    else:
        body_html = ""

    att_bits = "".join(_attachment_html(a) for a in atts if a["role"] in ("body", "sticker"))
    att_html = f'<div class="attachments">{att_bits}</div>' if att_bits else ""

    edited = ' <span class="edited">(bearbeitet)</span>' if row["is_edited"] else ""
    react_html = _reactions_html(reas)

    search_plain = f'{row["author"] or ""} {body} {row["quote_text"] or ""}'.lower()
    search_attr = html.escape(search_plain)

    return (
        f'<div class="{classes}" id="m{mid}" data-search="{search_attr}">'
        f"{author_html}{quote_html}{att_html}{body_html}"
        f'<div class="meta">{time_txt}{edited}</div>{react_html}'
        f"</div>"
    )


# --------------------------------------------------------------------- Stats
#
# query.stats()/response_times()/top_words() filtern Chats über ein
# `c.name LIKE '%chat%'` — bei generischen Namen (z. B. "Alex" als Substring
# von "Alexander Schneider") matcht das mehrere Chats zugleich. Da wir hier
# bereits die exakten, auf `chat_id` gefilterten Nachrichtenzeilen einer
# einzelnen Chat-Seite vorliegen haben, rechnen wir die Statistik lokal aus
# diesen Zeilen — ohne query.py anzufassen, aber ohne die Namens-Ambiguität.

_WORD_RE = re.compile(r"[\w'’-]+", re.UNICODE)


def _chat_overall(content: list[sqlite3.Row]) -> dict:
    return {
        "n": len(content),
        "sent": sum(1 for r in content if r["direction"] == "outgoing"),
        "received": sum(1 for r in content if r["direction"] == "incoming"),
        "attachments": sum(r["n_attachments"] or 0 for r in content),
    }


def _chat_by_author(content: list[sqlite3.Row]) -> list[dict]:
    agg: dict[str, dict] = {}
    for r in content:
        author = r["author"] or "?"
        a = agg.setdefault(author, {"author": author, "n": 0, "chars": 0, "attachments": 0})
        a["n"] += 1
        a["chars"] += len(r["body"] or "")
        a["attachments"] += r["n_attachments"] or 0
    return sorted(agg.values(), key=lambda x: -x["n"])


def _chat_by_month(content: list[sqlite3.Row]) -> list[dict]:
    agg: dict[str, int] = {}
    for r in content:
        stamp = ms_to_local(r["sent_at"])
        if not stamp:
            continue
        key = f"{stamp.year:04d}-{stamp.month:02d}"
        agg[key] = agg.get(key, 0) + 1
    return [{"bucket": k, "n": agg[k]} for k in sorted(agg)]


def _chat_by_hour(content: list[sqlite3.Row]) -> list[dict]:
    agg: dict[int, int] = {}
    for r in content:
        stamp = ms_to_local(r["sent_at"])
        if not stamp:
            continue
        agg[stamp.hour] = agg.get(stamp.hour, 0) + 1
    return [{"bucket": h, "n": agg[h]} for h in sorted(agg)]


def _chat_reactions(reas: dict) -> list[dict]:
    counts: dict[str, int] = {}
    for lst in reas.values():
        for r in lst:
            counts[r["emoji"]] = counts.get(r["emoji"], 0) + 1
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:15]
    return [{"emoji": e, "n": n} for e, n in top]


def _chat_calls(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    call_ids = [r["id"] for r in rows if r["kind"] == "call"]
    if not call_ids:
        return []
    placeholders = ",".join("?" * len(call_ids))
    return conn.execute(
        f"""SELECT call_type, direction, state, COUNT(*) AS n FROM calls
            WHERE message_id IN ({placeholders})
            GROUP BY call_type, direction, state ORDER BY n DESC""",
        call_ids,
    ).fetchall()


def _chat_response_times(rows: list[sqlite3.Row]) -> list[dict]:
    """Median-Antwortzeit je Person innerhalb dieses einen Chats (Pausen > 12 h ausgenommen)."""
    seq = [r for r in rows if r["kind"] in ("standard", "sticker")]
    gaps: dict[str, list[int]] = {}
    prev_author_id = None
    prev_time = None
    for r in seq:
        if prev_time is not None and r["author_id"] != prev_author_id:
            delta = r["sent_at"] - prev_time
            if 0 < delta <= 12 * 3600 * 1000:
                gaps.setdefault(r["author"] or "?", []).append(delta)
        prev_author_id = r["author_id"]
        prev_time = r["sent_at"]
    out = []
    for author, values in gaps.items():
        values.sort()
        mid = len(values) // 2
        median = values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) // 2
        out.append(
            {"author": author, "n": len(values), "median_s": median / 1000, "mean_s": sum(values) / len(values) / 1000}
        )
    out.sort(key=lambda x: x["median_s"])
    return out


def _chat_top_words(rows: list[sqlite3.Row], limit: int = 25, min_len: int = 4) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for r in rows:
        if r["kind"] != "standard" or not r["body"]:
            continue
        for word in _WORD_RE.findall(r["body"].lower()):
            if len(word) < min_len or word in query.STOPWORDS or word.isdigit():
                continue
            counts[word] = counts.get(word, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])[:limit]


def _chat_stats(conn: sqlite3.Connection, rows: list[sqlite3.Row], reas: dict) -> dict:
    content = [r for r in rows if r["kind"] in query.CONTENT_KINDS]
    return {
        "overall": _chat_overall(content),
        "by_author": _chat_by_author(content),
        "by_month": _chat_by_month(content),
        "by_hour": _chat_by_hour(content),
        "reactions": _chat_reactions(reas),
        "calls": _chat_calls(conn, rows),
        "response_times": _chat_response_times(rows),
        "top_words": _chat_top_words(rows),
    }


def _bar_rows(rows: list, label_key: str, value_key: str, label_fmt=str) -> str:
    if not rows:
        return ""
    max_n = max((r[value_key] or 0) for r in rows) or 1
    out = []
    for r in rows:
        n = r[value_key] or 0
        pct = round((n / max_n) * 100, 1)
        out.append(
            f'<div class="bar-row"><span class="bar-label">{html.escape(label_fmt(r[label_key]))}</span>'
            f'<span class="bar-track"><span class="bar-fill" style="width:{pct}%"></span></span>'
            f'<span class="bar-value">{n}</span></div>'
        )
    return "".join(out)


def _stats_html(stats_data: dict) -> str:
    ov = stats_data["overall"]
    parts: list[str] = ['<details class="stats"><summary>Statistik</summary><div class="stats-body">']
    parts.append(
        f'<p class="stats-overall">{ov["n"] or 0} Nachrichten · {ov["sent"] or 0} gesendet · '
        f'{ov["received"] or 0} empfangen · {ov["attachments"] or 0} Anhänge</p>'
    )

    parts.append("<h3>Nachrichten je Person</h3>")
    parts.append(
        '<div class="table-wrap"><table><thead><tr><th>Person</th><th>Nachrichten</th>'
        "<th>Zeichen</th><th>Anhänge</th></tr></thead><tbody>"
    )
    for r in stats_data["by_author"]:
        parts.append(
            f'<tr><td>{html.escape(r["author"] or "?")}</td><td>{r["n"]}</td>'
            f'<td>{r["chars"] or 0}</td><td>{r["attachments"] or 0}</td></tr>'
        )
    parts.append("</tbody></table></div>")

    months = _bar_rows(stats_data["by_month"], "bucket", "n")
    if months:
        parts.append(f'<h3>Nachrichten pro Monat</h3><div class="bars">{months}</div>')

    hours_map = {r["bucket"]: r["n"] for r in stats_data["by_hour"]}
    hour_rows = [{"bucket": h, "n": hours_map.get(h, 0)} for h in range(24)]
    hours = _bar_rows(hour_rows, "bucket", "n", label_fmt=lambda h: f"{h:02d}:00")
    if hours:
        parts.append(f'<h3>Aktivität nach Stunde</h3><div class="bars hours">{hours}</div>')

    reactions = stats_data["reactions"]
    if reactions:
        chips = "".join(f'<span class="chip">{html.escape(r["emoji"])} {r["n"]}</span>' for r in reactions)
        parts.append(f'<h3>Häufigste Reaktionen</h3><div class="chips">{chips}</div>')

    calls = stats_data["calls"]
    if calls:
        total = sum(r["n"] for r in calls)
        missed = sum(r["n"] for r in calls if r["state"] == "MISSED")
        accepted = sum(r["n"] for r in calls if r["state"] == "ACCEPTED")
        parts.append(
            f"<h3>Anrufbilanz</h3><p>{total} Anrufe insgesamt · {accepted} angenommen · "
            f"{missed} verpasst</p>"
        )
        parts.append(
            '<div class="table-wrap"><table><thead><tr><th>Art</th><th>Richtung</th>'
            "<th>Status</th><th>Anzahl</th></tr></thead><tbody>"
        )
        for r in calls:
            parts.append(
                f'<tr><td>{html.escape(r["call_type"] or "?")}</td>'
                f'<td>{html.escape(r["direction"] or "?")}</td>'
                f'<td>{html.escape(r["state"] or "?")}</td><td>{r["n"]}</td></tr>'
            )
        parts.append("</tbody></table></div>")

    rts = stats_data["response_times"]
    if rts:
        parts.append(
            "<h3>Median-Antwortzeit</h3>"
            '<div class="table-wrap"><table><thead><tr><th>Person</th><th>Antworten</th>'
            "<th>Median</th><th>Mittelwert</th></tr></thead><tbody>"
        )
        for r in rts:
            parts.append(
                f'<tr><td>{html.escape(r["author"])}</td><td>{r["n"]}</td>'
                f'<td>{_fmt_duration(r["median_s"])}</td><td>{_fmt_duration(r["mean_s"])}</td></tr>'
            )
        parts.append("</tbody></table></div>")

    words = stats_data["top_words"]
    if words:
        chips = "".join(f'<span class="chip">{html.escape(w)} <b>{n}</b></span>' for w, n in words)
        parts.append(f'<h3>Häufigste Wörter</h3><div class="chips">{chips}</div>')

    parts.append("</div></details>")
    return "".join(parts)


# ----------------------------------------------------------------------- CSS/JS

_CSS = """
:root {
  --bg: #f2f2f5; --bg-panel: #ffffff; --text: #1b1b1f; --text-muted: #6b6b74;
  --border: #dcdce2; --bubble-in: #ffffff; --bubble-out: #d9fdd3;
  --bubble-out-text: #0b2e13; --accent: #2563eb; --sys-bg: #e7e7ec;
  --link: #2563eb; --danger: #b3261e; --shadow: 0 1px 2px rgba(0,0,0,.08);
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #101013; --bg-panel: #1a1a1f; --text: #e7e7ec; --text-muted: #9a9aa4;
    --border: #2c2c33; --bubble-in: #24242b; --bubble-out: #1f4620;
    --bubble-out-text: #d8f5d0; --accent: #6ea8fe; --sys-bg: #202027;
    --link: #6ea8fe; --danger: #ff8a80; --shadow: 0 1px 2px rgba(0,0,0,.4);
  }
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  background: var(--bg); color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  font-size: 15px; line-height: 1.4;
}
a { color: var(--link); }
h1, h2, h3 { margin: .4em 0; }
header.top {
  position: sticky; top: 0; z-index: 20; background: var(--bg-panel);
  border-bottom: 1px solid var(--border); padding: .6rem 1rem; box-shadow: var(--shadow);
}
header.top h1 { font-size: 1.05rem; }
.meta-line { color: var(--text-muted); font-size: .85rem; margin: .15rem 0; }
.meta-line a.back { margin-right: .6rem; }
.search-bar { display: flex; gap: .4rem; align-items: center; margin-top: .4rem; flex-wrap: wrap; }
.search-bar input {
  flex: 1 1 200px; min-width: 120px; padding: .35rem .6rem; border-radius: 999px;
  border: 1px solid var(--border); background: var(--bg); color: var(--text);
}
.search-bar button {
  border: 1px solid var(--border); background: var(--bg-panel); color: var(--text);
  border-radius: 999px; padding: .3rem .7rem; cursor: pointer;
}
.search-bar button:hover { background: var(--sys-bg); }
#sv-search-count { color: var(--text-muted); font-size: .85rem; min-width: 4.5em; }

.stats { margin: .6rem 1rem; background: var(--bg-panel); border: 1px solid var(--border);
  border-radius: .6rem; padding: .3rem .8rem .8rem; }
.stats summary { cursor: pointer; font-weight: 600; padding: .5rem 0; }
.stats-overall { color: var(--text-muted); }
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: .9rem; }
th, td { text-align: left; padding: .25rem .6rem; border-bottom: 1px solid var(--border); }
.bars { display: flex; flex-direction: column; gap: .15rem; margin: .3rem 0; }
.bar-row { display: grid; grid-template-columns: 6.5em 1fr 3em; align-items: center; gap: .5rem; font-size: .8rem; }
.bar-track { background: var(--sys-bg); border-radius: 999px; height: .6rem; overflow: hidden; }
.bar-fill { display: block; height: 100%; background: var(--accent); }
.chips { display: flex; flex-wrap: wrap; gap: .3rem; margin: .3rem 0; }
.chip { background: var(--sys-bg); border-radius: 999px; padding: .15rem .6rem; font-size: .85rem; }

main.chat, main.index-main { max-width: 900px; margin: 0 auto; padding: .6rem 1rem 3rem; }

.day { margin-top: .4rem; }
.day-sep {
  position: sticky; top: 88px; z-index: 10; text-align: center; color: var(--text-muted);
  font-size: .78rem; background: var(--bg); padding: .4rem 0; margin: 0 auto;
}
.sys {
  text-align: center; color: var(--text-muted); background: var(--sys-bg);
  border-radius: .6rem; padding: .3rem .6rem; margin: .35rem auto; max-width: 80%;
  font-size: .82rem;
}
.sys-time { opacity: .7; margin-right: .3rem; }

.msg {
  max-width: 78%; margin: .2rem 0; padding: .4rem .6rem; border-radius: .7rem;
  background: var(--bubble-in); box-shadow: var(--shadow); position: relative;
}
.msg.out { margin-left: auto; background: var(--bubble-out); color: var(--bubble-out-text); }
.msg.in { margin-right: auto; }
.msg .author { font-size: .78rem; font-weight: 600; color: var(--accent); margin-bottom: .1rem; }
.msg .body { white-space: normal; word-wrap: break-word; }
.msg .body.deleted-text { font-style: italic; color: var(--text-muted); }
.msg .meta { font-size: .7rem; color: var(--text-muted); text-align: right; margin-top: .15rem; }
.msg.out .meta { color: rgba(11,46,19,.6); }
.msg .edited { font-style: italic; }
.quote {
  display: block; border-left: 3px solid var(--accent); padding: .1rem .5rem; margin: .1rem 0 .3rem;
  font-size: .82rem; color: var(--text-muted); background: rgba(127,127,127,.08); border-radius: .2rem;
  text-decoration: none;
}
.quote-author { font-weight: 600; }
.attachments { display: flex; flex-direction: column; gap: .3rem; margin: .2rem 0; }
.att-image img { max-width: min(320px, 70vw); max-height: 320px; border-radius: .4rem; cursor: zoom-in; display: block; }
.att-audio { display: flex; align-items: center; gap: .3rem; }
.att-audio audio, .msg video { max-width: min(320px, 80vw); }
.msg video { border-radius: .4rem; display: block; }
.att-file { display: inline-block; }
.att-missing {
  color: var(--text-muted); font-size: .82rem; background: rgba(127,127,127,.12);
  border-radius: .3rem; padding: .2rem .5rem;
}
.reactions { margin-top: .2rem; display: flex; gap: .2rem; flex-wrap: wrap; }
.reaction { background: var(--bg-panel); border: 1px solid var(--border); border-radius: 999px;
  padding: .05rem .4rem; font-size: .78rem; }
.msg.sv-current { outline: 2px solid var(--accent); }

.sv-lightbox {
  display: none; position: fixed; inset: 0; background: rgba(0,0,0,.85); z-index: 100;
  align-items: center; justify-content: center; cursor: zoom-out;
}
.sv-lightbox.open { display: flex; }
.sv-lightbox img { max-width: 95vw; max-height: 95vh; }

.backup-group { margin: 1rem 0 1.4rem; }
.backup-group h2 { margin-bottom: .1rem; }
.backup-group .meta-line { margin-bottom: .4rem; }
"""

_JS = r"""
(function () {
  "use strict";
  function all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  var overlay = document.createElement("div");
  overlay.className = "sv-lightbox";
  overlay.innerHTML = '<img alt="">';
  overlay.addEventListener("click", function () { overlay.classList.remove("open"); });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") overlay.classList.remove("open");
  });
  document.body.appendChild(overlay);
  window.svLightbox = function (wrapper) {
    var img = wrapper.querySelector("img");
    if (!img) return;
    overlay.querySelector("img").src = img.src;
    overlay.classList.add("open");
  };

  var input = document.getElementById("sv-search");
  if (!input) return;
  var countEl = document.getElementById("sv-search-count");
  var prevBtn = document.getElementById("sv-search-prev");
  var nextBtn = document.getElementById("sv-search-next");
  var msgs = all(".msg, .sys");
  var days = all(".day");
  var matches = [];
  var current = -1;

  function dayHasVisible(day) {
    return all(".msg, .sys", day).some(function (m) { return m.style.display !== "none"; });
  }
  function clearHighlight() {
    if (current >= 0 && matches[current]) matches[current].classList.remove("sv-current");
  }
  function goto(i) {
    if (!matches.length) return;
    clearHighlight();
    current = ((i % matches.length) + matches.length) % matches.length;
    var el = matches[current];
    el.classList.add("sv-current");
    el.scrollIntoView({ block: "center", behavior: "smooth" });
    countEl.textContent = (current + 1) + " / " + matches.length;
  }
  function runSearch() {
    var term = input.value.trim().toLowerCase();
    clearHighlight();
    matches = [];
    current = -1;
    if (!term) {
      msgs.forEach(function (m) { m.style.display = ""; });
      days.forEach(function (d) { d.style.display = ""; });
      countEl.textContent = "";
      return;
    }
    msgs.forEach(function (m) {
      var hay = m.getAttribute("data-search") || "";
      var hit = hay.indexOf(term) !== -1;
      m.style.display = hit ? "" : "none";
      if (hit) matches.push(m);
    });
    days.forEach(function (d) { d.style.display = dayHasVisible(d) ? "" : "none"; });
    countEl.textContent = matches.length ? ("0 / " + matches.length) : "kein Treffer";
    if (matches.length) goto(0);
  }
  var debounce;
  input.addEventListener("input", function () {
    clearTimeout(debounce);
    debounce = setTimeout(runSearch, 120);
  });
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter") {
      e.preventDefault();
      if (e.shiftKey) goto(current - 1); else goto(current + 1);
    }
  });
  if (nextBtn) nextBtn.addEventListener("click", function () { goto(current + 1); });
  if (prevBtn) prevBtn.addEventListener("click", function () { goto(current - 1); });
})();
"""

_HEAD = (
    '<meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
)


# ------------------------------------------------------------------ Chat-Seite


def _render_chat_page(chat_name: str, backup_label: str, rows: list[sqlite3.Row],
                       atts: dict, reas: dict, stats_data: dict) -> str:
    title = html.escape(chat_name)
    first_ms, last_ms = rows[0]["sent_at"], rows[-1]["sent_at"]
    period = _fmt_period_ms(first_ms, last_ms)

    body_parts: list[str] = []
    last_day = None
    last_speaker = None
    for row in rows:
        stamp = ms_to_local(row["sent_at"])
        day = stamp.date() if stamp else None
        if day != last_day:
            if last_day is not None:
                body_parts.append("</section>")
            last_day = day
            last_speaker = None
            day_id = day.isoformat() if day else "unbekannt"
            heading = _fmt_date_heading(stamp) if stamp else "Unbekanntes Datum"
            body_parts.append(f'<section class="day" id="day-{day_id}">')
            body_parts.append(f'<div class="day-sep">{html.escape(heading)}</div>')
        kind = row["kind"]
        speaker_key = (row["author_id"], row["direction"])
        show_author = kind not in ("update", "call") and speaker_key != last_speaker
        if kind not in ("update", "call"):
            last_speaker = speaker_key
        body_parts.append(
            _message_html(row, atts.get(row["id"], []), reas.get(row["id"], []), show_author)
        )
    if last_day is not None:
        body_parts.append("</section>")

    stats_html = _stats_html(stats_data)
    backup_line = f' · Backup: {html.escape(backup_label)}' if backup_label else ""

    doc = [
        "<!DOCTYPE html>",
        '<html lang="de"><head>',
        _HEAD,
        f"<title>{title} – chappe</title>",
        f"<style>{_CSS}</style>",
        "</head><body>",
        '<header class="top">',
        f"<h1>{title}</h1>",
        f'<div class="meta-line"><a class="back" href="index.html">&larr; Übersicht</a>'
        f"{len(rows)} Nachrichten · {html.escape(period)}{backup_line}</div>",
        '<div class="search-bar">'
        '<input id="sv-search" type="search" placeholder="Suchen …" autocomplete="off">'
        '<button id="sv-search-prev" type="button" title="Vorheriger Treffer">&uarr;</button>'
        '<button id="sv-search-next" type="button" title="Nächster Treffer">&darr;</button>'
        '<span id="sv-search-count"></span>'
        "</div>",
        "</header>",
        '<main class="chat">',
        stats_html,
        "".join(body_parts),
        "</main>",
        f"<script>{_JS}</script>",
        "</body></html>",
    ]
    return "".join(doc)


def _unique_filename(chat_name: str, backup_label: str, chat_id: int, grouped: bool, registry: dict) -> str:
    if grouped:
        base = f"{safe_filename(backup_label, 40)}__{safe_filename(chat_name, 60)}"
    else:
        base = safe_filename(chat_name, 60) or "chat"
    candidate = base + ".html"
    if candidate not in registry:
        registry[candidate] = True
        return candidate
    candidate = f"{base}_{chat_id}.html"
    registry[candidate] = True
    return candidate


# -------------------------------------------------------------------- Index


def _chat_table_html(entries: list[dict]) -> str:
    parts = [
        '<div class="table-wrap"><table><thead><tr><th>Name</th><th>Nachrichten</th>'
        "<th>Gesendet</th><th>Empfangen</th><th>Anhänge</th><th>Zeitraum</th></tr></thead><tbody>"
    ]
    for c in entries:
        href = quote(c["file"])
        parts.append(
            f'<tr><td><a href="{href}">{html.escape(c["name"])}</a></td>'
            f'<td>{c["messages"]}</td><td>{c["sent"]}</td><td>{c["received"]}</td>'
            f'<td>{c["attachments"]}</td><td>{html.escape(c["period"])}</td></tr>'
        )
    parts.append("</tbody></table></div>")
    return "".join(parts)


def _render_index(entries: list[dict], backups_rows: list[sqlite3.Row], grouped: bool) -> str:
    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="de"><head>',
        _HEAD,
        "<title>chappe – Übersicht</title>",
        f"<style>{_CSS}</style>",
        "</head><body>",
        '<header class="top"><h1>chappe – Chat-Übersicht</h1></header>',
        '<main class="index-main">',
    ]

    if grouped:
        by_backup: dict[str, list[dict]] = {}
        for e in entries:
            by_backup.setdefault(e["backup"], []).append(e)
        for b in backups_rows:
            label = b["label"]
            chats = by_backup.get(label, [])
            if not chats:
                continue
            first_ms = min((c["first_ms"] for c in chats if c["first_ms"] is not None), default=None)
            last_ms = max((c["last_ms"] for c in chats if c["last_ms"] is not None), default=None)
            period = _fmt_period_ms(first_ms, last_ms)
            parts.append('<section class="backup-group">')
            parts.append(f"<h2>{html.escape(label)}</h2>")
            parts.append(
                f'<div class="meta-line">Account: {html.escape(b["self_name"] or "?")} · '
                f"{html.escape(period)} · {b['media_files_bound'] or 0} / "
                f"{b['media_files_total'] or 0} Mediendateien gebunden</div>"
            )
            parts.append(_chat_table_html(chats))
            parts.append("</section>")
    else:
        if backups_rows:
            b = backups_rows[0]
            created = ms_to_local(b["backup_time_ms"])
            parts.append(
                f'<p class="meta-line">Backup {html.escape(b["label"])} · '
                f'Account: {html.escape(b["self_name"] or "?")} · '
                f'erstellt {f"{created:%d.%m.%Y %H:%M}" if created else "?"} · '
                f"{b['media_files_bound'] or 0} / {b['media_files_total'] or 0} Mediendateien gebunden</p>"
            )
        parts.append(_chat_table_html(entries))

    parts.append("</main></body></html>")
    return "".join(parts)


# ---------------------------------------------------------------- write_html


def write_html(
    conn: sqlite3.Connection,
    out_dir: str | Path,
    *,
    backup: str | None = None,
    chat: str | None = None,
    since: str | None = None,
    until: str | None = None,
    media: str = "link",  # link | copy | none
    progress=None,
) -> list[Path]:
    """Schreibt index.html + je Chat eine HTML-Datei. Gibt die geschriebenen Pfade zurück."""
    progress = progress or (lambda _m: None)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    media_exported = media != "none"
    if media_exported:
        progress("Exportiere Medien …")
        export_media(conn, out / "media", backup=backup, chat=chat, mode=media,
                     group_by_chat=True, progress=progress)

    total_backups = conn.execute("SELECT COUNT(*) AS n FROM backups").fetchone()["n"]
    grouped = backup is None and total_backups > 1

    chats = query.list_chats(conn, backup=backup)
    if chat:
        needle = chat.lower()
        chats = [c for c in chats if needle in (c["chat"] or "").lower()]

    registry: dict[str, bool] = {}
    entries: list[dict] = []
    written: list[Path] = []

    for chat_row in chats:
        chat_name = chat_row["chat"]
        chat_backup = chat_row["backup"]
        progress(f"Chat: {chat_name} ({chat_backup}) …")

        # chat=<name> filtert bei query.transcript über `c.name LIKE '%name%'` — das
        # matcht bei generischen Namen (z. B. "Alex" als Substring von "Alexander
        # Schneider") mehr als den einen gemeinten Chat. Wir grenzen deshalb hier
        # zusätzlich exakt auf die chat_id dieser Zeile aus list_chats ein.
        rows = query.transcript(
            conn, backup=chat_backup, chat=chat_name, since=since, until=until, ascending=True
        )
        rows = [r for r in rows if r["chat_id"] == chat_row["chat_id"]]
        if not rows:
            continue

        ids = [r["id"] for r in rows]
        atts = query.attachments_for(conn, ids)
        reas = query.reactions_for(conn, ids)
        stats_data = _chat_stats(conn, rows, reas)

        filename = _unique_filename(chat_name, chat_backup, chat_row["chat_id"], grouped, registry)
        page = _render_chat_page(chat_name, chat_backup if grouped else "", rows, atts, reas, stats_data)
        path = out / filename
        path.write_text(page, encoding="utf-8")
        written.append(path)

        sent = sum(1 for r in rows if r["direction"] == "outgoing")
        received = sum(1 for r in rows if r["direction"] == "incoming")
        att_count = sum(r["n_attachments"] or 0 for r in rows)
        entries.append(
            {
                "name": chat_name,
                "backup": chat_backup,
                "file": filename,
                "messages": len(rows),
                "sent": sent,
                "received": received,
                "attachments": att_count,
                "first_ms": rows[0]["sent_at"],
                "last_ms": rows[-1]["sent_at"],
                "period": _fmt_period_ms(rows[0]["sent_at"], rows[-1]["sent_at"]),
            }
        )

    backups_sql = "SELECT * FROM backups"
    backups_params: list = []
    if backup:
        backups_sql += " WHERE label = ?"
        backups_params.append(backup)
    backups_sql += " ORDER BY id"
    backups_rows = conn.execute(backups_sql, backups_params).fetchall()

    progress("Schreibe index.html …")
    index_html = _render_index(entries, backups_rows, grouped)
    index_path = out / "index.html"
    index_path.write_text(index_html, encoding="utf-8")
    written.append(index_path)

    return written
