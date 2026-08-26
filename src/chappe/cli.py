"""Kommandozeile für chappe: Import, Suche, Auswertung und Export."""

from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

from . import query
from .importer import connect, import_backup
from .media import export_media
from .model import ms_to_local, safe_filename
from .render.markdown import render_transcript, write_markdown

WEEKDAYS = ["Sonntag", "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag"]


class CliError(Exception):
    """Fehler, die dem Nutzer als klare deutsche Meldung angezeigt werden — kein Traceback."""


# ------------------------------------------------------------------ Darstellung


def _human_size(n: int | None) -> str:
    if not n:
        return "0 B"
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def _human_duration(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    total = int(round(seconds))
    if total < 60:
        return f"{total} s"
    minutes, sec = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} min {sec} s" if sec else f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} h {minutes} min" if minutes else f"{hours} h"
    days, hours = divmod(hours, 24)
    return f"{days} d {hours} h" if hours else f"{days} d"


def _fmt_ts(ms: int | None) -> str:
    dt = ms_to_local(ms)
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "?"


def _short(body: str | None, limit: int = 100) -> str:
    if not body:
        return "(leer)"
    flat = " ".join(body.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _cells(values) -> list[str]:
    return ["" if v is None else str(v) for v in values]


def format_table(headers: list[str], rows: list[list]) -> str:
    """Tabellenausgabe mit dynamischer Spaltenbreite; kürzt lange Werte mit '…'."""
    str_rows = [_cells(r) for r in rows]
    if not str_rows:
        return "(keine Daten)"
    ncols = len(headers)
    widths = [len(h) for h in headers]
    for row in str_rows:
        for i in range(ncols):
            widths[i] = max(widths[i], len(row[i]) if i < len(row) else 0)

    term_width = shutil.get_terminal_size((120, 24)).columns
    sep = 2
    total = sum(widths) + sep * (ncols - 1)
    overflow = total - term_width
    while overflow > 0:
        idx = max(range(ncols), key=lambda i: widths[i])
        if widths[idx] <= 6:
            break
        widths[idx] -= 1
        overflow -= 1

    def fit(val: str, width: int) -> str:
        if len(val) <= width:
            return val.ljust(width)
        if width <= 1:
            return val[:width]
        return (val[: width - 1] + "…").ljust(width)

    lines = ["  ".join(fit(h, w) for h, w in zip(headers, widths))]
    lines.append("  ".join("─" * w for w in widths))
    for row in str_rows:
        cells = row + [""] * (ncols - len(row))
        lines.append("  ".join(fit(cells[i], widths[i]) for i in range(ncols)))
    return "\n".join(lines)


def _bar_chart(items: list[tuple[str, int]]) -> str:
    if not items:
        return "(keine Daten)"
    term_width = shutil.get_terminal_size((100, 24)).columns
    label_width = max(len(str(label)) for label, _ in items)
    max_val = max((v or 0) for _, v in items) or 1
    num_width = max(len(str(v or 0)) for _, v in items)
    bar_space = max(10, term_width - label_width - num_width - 4)
    lines = []
    for label, val in items:
        val = val or 0
        filled = round((val / max_val) * bar_space) if max_val else 0
        bar = "█" * filled
        lines.append(f"{str(label).rjust(label_width)}  {bar} {val}")
    return "\n".join(lines)


def _jsonable(obj):
    if isinstance(obj, sqlite3.Row):
        return {k: _jsonable(obj[k]) for k in obj.keys()}
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


def _print_json(obj) -> None:
    print(json.dumps(_jsonable(obj), ensure_ascii=False, indent=2))


# ------------------------------------------------------------------ Filter/Backup/Chat


def _collect_filters(args, backup: str | None = None) -> dict:
    filters = {}
    for key in ("chat", "author", "since", "until"):
        val = getattr(args, key, None)
        if val:
            filters[key] = val
    if backup:
        filters["backup"] = backup
    return filters


def _known_backups(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT b.label AS label, COUNT(m.id) AS n
           FROM backups b LEFT JOIN messages m ON m.backup_id = b.id
           GROUP BY b.id ORDER BY b.label"""
    ).fetchall()


def _resolve_backup(args, conn: sqlite3.Connection, *, required_context: bool) -> str | None:
    """Liefert das zu verwendende Backup-Label, oder None für 'alle Backups'.

    required_context=True gilt für Befehle mit genau einem sinnvollen Kontext
    (show, stats, export): bei mehreren Backups wird ohne --all-backups eine
    explizite Wahl verlangt. Für chats/search/media (required_context=False)
    ist 'alle' der stillschweigende Default.
    """
    explicit = getattr(args, "backup", None)
    if explicit:
        return explicit

    backups = _known_backups(conn)
    if len(backups) <= 1:
        return None

    if getattr(args, "all_backups", False):
        print(
            "WARNUNG: Auswertung über mehrere Backups hinweg — überlappende Backups "
            "(derselbe Chat aus zwei Perspektiven, mit vertauschter Richtung) zählen "
            "gemeinsame Nachrichten dabei möglicherweise doppelt.",
            file=sys.stderr,
        )
        return None

    if not required_context:
        return None

    if sys.stdin.isatty():
        print("Mehrere Backups vorhanden. Welches?", file=sys.stderr)
        for i, b in enumerate(backups, 1):
            print(f"  [{i}] {b['label']} ({b['n']} Nachrichten)", file=sys.stderr)
        print("  [a] alle (überlappend — zählt ggf. doppelt)", file=sys.stderr)
        choice = input("Auswahl: ").strip().lower()
        if choice in ("a", "alle", "all"):
            print(
                "WARNUNG: Auswertung über mehrere Backups hinweg — Nachrichten können "
                "doppelt gezählt werden.",
                file=sys.stderr,
            )
            return None
        try:
            idx = int(choice)
        except ValueError:
            idx = -1
        if 1 <= idx <= len(backups):
            return backups[idx - 1]["label"]
        raise CliError(f"Ungültige Auswahl: {choice!r}")

    names = ", ".join(b["label"] for b in backups)
    raise CliError(
        f"Mehrere Backups vorhanden ({names}) — bitte --backup LABEL angeben "
        f"oder --all-backups für eine (überlappende) Gesamtauswertung."
    )


def _resolve_chat(conn: sqlite3.Connection, chat_filter: str | None, backup: str | None) -> sqlite3.Row:
    chats = query.list_chats(conn, backup=backup)
    if not chats:
        raise CliError("Keine Chats in der Datenbank." if not backup else
                        f"Keine Chats für Backup '{backup}'.")
    if chat_filter:
        needle = chat_filter.lower()
        matching = [c for c in chats if needle in (c["chat"] or "").lower()]
        if not matching:
            names = "\n".join(f"  - {c['chat']}" for c in chats)
            raise CliError(
                f"Kein Chat gefunden, der zu '{chat_filter}' passt. Verfügbare Chats:\n{names}"
            )
    else:
        matching = chats
    if len(matching) > 1:
        names = "\n".join(
            f"  - {c['chat']} ({c['messages']} Nachrichten)" for c in matching
        )
        raise CliError(
            "Mehrdeutig — --chat genauer angeben. Verfügbare Chats:\n" + names
        )
    return matching[0]


def _context_rows(conn: sqlite3.Connection, chat_id: int, sent_at: int, msg_id: int, n: int):
    before = conn.execute(
        """SELECT m.id, m.sent_at, COALESCE(r.display_name,'?') AS author, m.body, m.kind
           FROM messages m LEFT JOIN recipients r ON r.id = m.author_id
           WHERE m.chat_id = ? AND m.revision_of IS NULL
             AND (m.sent_at < ? OR (m.sent_at = ? AND m.id < ?))
           ORDER BY m.sent_at DESC, m.id DESC LIMIT ?""",
        (chat_id, sent_at, sent_at, msg_id, n),
    ).fetchall()
    after = conn.execute(
        """SELECT m.id, m.sent_at, COALESCE(r.display_name,'?') AS author, m.body, m.kind
           FROM messages m LEFT JOIN recipients r ON r.id = m.author_id
           WHERE m.chat_id = ? AND m.revision_of IS NULL
             AND (m.sent_at > ? OR (m.sent_at = ? AND m.id > ?))
           ORDER BY m.sent_at ASC, m.id ASC LIMIT ?""",
        (chat_id, sent_at, sent_at, msg_id, n),
    ).fetchall()
    return list(reversed(before)), after


# ------------------------------------------------------------------ Quellen-Konfiguration


def _config_path(args) -> Path:
    explicit = getattr(args, "config", None)
    if explicit:
        return Path(explicit)
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "chappe" / "sources.json"


def _load_sources(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise CliError(f"Quellen-Konfiguration {path} ist beschädigt: {exc}")
    if not isinstance(data, list):
        raise CliError(f"Quellen-Konfiguration {path} hat ein unerwartetes Format (erwartet: Liste).")
    return data


def _save_sources(path: Path, sources: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sources, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ import


def _print_import_result(args, report, media_result) -> None:
    if args.json:
        payload = dataclasses.asdict(report)
        if media_result is not None:
            payload["media_export"] = dataclasses.asdict(media_result)
        _print_json(payload)
    else:
        print("\n".join(report.lines()))
        if media_result is not None:
            print()
            print(
                f"Medien exportiert: {media_result.exported}, "
                f"wiederverwendet: {media_result.reused}, "
                f"fehlend: {media_result.missing}, "
                f"geschrieben: {_human_size(media_result.bytes_written)}"
            )


def _import_many(args, conn, targets: list[tuple[str, Path]], progress) -> int:
    reports = []
    failed = []
    single = len(targets) == 1
    for name, target_dir in targets:
        if not args.quiet:
            print(f"--- {name} ---", file=sys.stderr)
        try:
            report = import_backup(
                conn,
                target_dir,
                label=(args.label if single else None) or name,
                replace=args.replace,
                keep_secrets=args.keep_secrets,
                progress=progress,
            )
        except (FileNotFoundError, ValueError) as exc:
            failed.append((name, str(exc)))
            print(f"Fehler bei '{name}': {exc}", file=sys.stderr)
            continue
        reports.append(report)
        media_result = None
        if args.media:
            media_result = export_media(conn, args.media, backup=report.label, progress=progress)
        if not args.json:
            print("\n".join(report.lines()))
            if media_result is not None:
                print(
                    f"Medien exportiert: {media_result.exported}, "
                    f"wiederverwendet: {media_result.reused}, "
                    f"fehlend: {media_result.missing}"
                )
            print()

    if args.json:
        _print_json(
            {
                "importiert": [dataclasses.asdict(r) for r in reports],
                "fehlgeschlagen": [{"quelle": n, "fehler": e} for n, e in failed],
            }
        )
    else:
        print("=== Sammelbilanz ===")
        print(f"Erfolgreich importiert: {len(reports)}")
        print(f"Nachrichten gesamt:     {sum(r.messages for r in reports)}")
        if failed:
            print(f"Fehlgeschlagen:         {len(failed)}")
            for name, err in failed:
                print(f"  - {name}: {err}")
    return 1 if failed else 0


def cmd_import(args, conn) -> int:
    if args.keep_secrets:
        print(
            "WARNUNG: --keep-secrets aktiv — Schlüsselmaterial (u. a. SVR-PIN, "
            "Profil-, Master- und Backup-Schlüssel) wird ungeschützt in die "
            "Datenbank geschrieben.",
            file=sys.stderr,
        )
    progress = None if args.quiet else (lambda msg: print(msg, file=sys.stderr))

    if args.all_sources:
        sources = _load_sources(_config_path(args))
        if not sources:
            raise CliError("Keine Quellen registriert — zuerst 'sources add <pfad>' ausführen.")
        return _import_many(args, conn, [(s["name"], Path(s["path"])) for s in sources], progress)

    if args.backup_dir:
        target_dir = Path(args.backup_dir)
        report = import_backup(
            conn, target_dir, label=args.label, replace=args.replace,
            keep_secrets=args.keep_secrets, progress=progress,
        )
        media_result = None
        if args.media:
            media_result = export_media(conn, args.media, backup=report.label, progress=progress)
        _print_import_result(args, report, media_result)
        return 0

    sources = _load_sources(_config_path(args))
    if not sources:
        raise CliError(
            "Kein Backup-Verzeichnis angegeben und keine Quellen registriert. "
            "Pfad angeben oder zuerst 'sources add <pfad>' ausführen."
        )
    if not sys.stdin.isatty():
        names = ", ".join(s["name"] for s in sources)
        raise CliError(
            f"Kein Backup-Verzeichnis angegeben. Verfügbare Quellen: {names}. "
            f"Pfad angeben oder --all-sources nutzen (interaktive Auswahl braucht ein Terminal)."
        )
    print("Welche Quelle(n) importieren?", file=sys.stderr)
    for i, s in enumerate(sources, 1):
        print(f"  [{i}] {s['name']} ({s['path']})", file=sys.stderr)
    choice = input("Auswahl (z. B. 1 oder 1,3 oder 'alle'): ").strip().lower()
    if choice in ("alle", "all", "a"):
        chosen = sources
    else:
        chosen = []
        for part in choice.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                idx = int(part)
            except ValueError:
                raise CliError(f"Ungültige Auswahl: {part!r}")
            if not (1 <= idx <= len(sources)):
                raise CliError(f"Ungültige Auswahl: {idx}")
            chosen.append(sources[idx - 1])
        if not chosen:
            raise CliError("Keine Auswahl getroffen.")
    return _import_many(args, conn, [(s["name"], Path(s["path"])) for s in chosen], progress)


# ------------------------------------------------------------------ sources


def _sources_list(args, conn, path, sources) -> int:
    if not sources:
        if args.json:
            print("[]")
        else:
            print(f"Keine Quellen registriert ({path}). Mit 'sources add <pfad>' hinzufügen.")
        return 0
    imported_paths = {
        row["source_path"] for row in conn.execute("SELECT source_path FROM backups").fetchall()
    }
    rows = []
    for src in sources:
        resolved = str(Path(src["path"]).resolve())
        imported = resolved in imported_paths or src["path"] in imported_paths
        rows.append({"name": src["name"], "path": src["path"], "importiert": imported})
    if args.json:
        _print_json(rows)
        return 0
    headers = ["Name", "Pfad", "Importiert"]
    table_rows = [[r["name"], r["path"], "ja" if r["importiert"] else "nein"] for r in rows]
    print(format_table(headers, table_rows))
    return 0


def _sources_add(args, path, sources) -> int:
    src_path = Path(args.path)
    if not (src_path / "main.jsonl").is_file():
        raise CliError(f"{src_path} enthält keine main.jsonl — kein Signal-Export?")
    name = args.name or src_path.name
    if any(s["name"] == name for s in sources):
        raise CliError(f"Es gibt bereits eine Quelle namens '{name}'.")
    sources.append({"name": name, "path": str(src_path.resolve())})
    _save_sources(path, sources)
    print(f"Quelle '{name}' registriert: {src_path.resolve()}")
    return 0


def _sources_remove(args, path, sources) -> int:
    remaining = [s for s in sources if s["name"] != args.name]
    if len(remaining) == len(sources):
        raise CliError(f"Keine Quelle namens '{args.name}' gefunden.")
    _save_sources(path, remaining)
    print(f"Quelle '{args.name}' entfernt.")
    return 0


def _sources_scan(args, path, sources) -> int:
    root = Path(args.directory)
    if not root.is_dir():
        raise CliError(f"Verzeichnis nicht gefunden: {root}")
    found = [c for c in sorted(root.iterdir()) if c.is_dir() and (c / "main.jsonl").is_file()]
    if not found:
        print("Keine Signal-Exporte gefunden (main.jsonl fehlt in allen Unterordnern).")
        return 0
    existing_paths = {str(Path(s["path"]).resolve()) for s in sources}
    existing_names = {s["name"] for s in sources}
    interactive = sys.stdin.isatty()
    added = 0
    for candidate in found:
        resolved = str(candidate.resolve())
        if resolved in existing_paths:
            print(f"  bereits registriert: {candidate.name}")
            continue
        if interactive:
            answer = input(f"Registrieren: {candidate.name} ({resolved})? [J/n] ").strip().lower()
            if answer not in ("", "j", "ja", "y", "yes"):
                print(f"  übersprungen: {candidate.name}")
                continue
        name, i = candidate.name, 2
        while name in existing_names:
            name = f"{candidate.name}_{i}"
            i += 1
        sources.append({"name": name, "path": resolved})
        existing_paths.add(resolved)
        existing_names.add(name)
        added += 1
        print(f"  registriert: {name}")
    if added:
        _save_sources(path, sources)
    print(f"{added} von {len(found)} gefundenen Exporten registriert.")
    return 0


def cmd_sources(args, conn) -> int:
    path = _config_path(args)
    sources = _load_sources(path)
    action = getattr(args, "sources_command", None)
    if action is None:
        return _sources_list(args, conn, path, sources)
    if action == "add":
        return _sources_add(args, path, sources)
    if action == "remove":
        return _sources_remove(args, path, sources)
    if action == "scan":
        return _sources_scan(args, path, sources)
    raise CliError(f"Unbekannter sources-Unterbefehl: {action}")


# ------------------------------------------------------------------ chats/search/show


def cmd_chats(args, conn) -> int:
    backup = _resolve_backup(args, conn, required_context=False)
    rows = query.list_chats(conn, backup=backup)
    if args.json:
        _print_json([dict(r) for r in rows])
        return 0
    if not rows:
        print("Keine Chats gefunden.")
        return 0
    headers = ["ID", "Name", "Nachrichten", "Gesendet", "Empfangen", "Anhänge", "Zeitraum"]
    table_rows = []
    for r in rows:
        first = (r["first_message"] or "?")[:10]
        last = (r["last_message"] or "?")[:10]
        table_rows.append(
            [r["chat_id"], r["chat"], r["messages"], r["sent"] or 0, r["received"] or 0,
             r["attachments"] or 0, f"{first} – {last}"]
        )
    print(format_table(headers, table_rows))
    return 0


def _print_context(conn, row, n: int) -> None:
    chat_id_row = conn.execute("SELECT chat_id FROM messages WHERE id = ?", (row["id"],)).fetchone()
    if not chat_id_row:
        return
    before, after = _context_rows(conn, chat_id_row["chat_id"], row["sent_at"], row["id"], n)
    for r in before:
        print(f"      {_fmt_ts(r['sent_at'])}  {r['author']}: {_short(r['body'])}")
    print(f"  →   {_fmt_ts(row['sent_at'])}  {row['author']}: {(row['snippet'] or row['body'] or '').strip()}")
    for r in after:
        print(f"      {_fmt_ts(r['sent_at'])}  {r['author']}: {_short(r['body'])}")
    print()


def cmd_search(args, conn) -> int:
    backup = _resolve_backup(args, conn, required_context=False)
    filters = _collect_filters(args, backup=backup)
    try:
        if args.literal:
            rows = query.search_like(conn, args.query, limit=args.limit, **filters)
        else:
            rows = query.search(conn, args.query, limit=args.limit, **filters)
    except sqlite3.OperationalError as exc:
        raise CliError(
            f"Suchanfrage ungültig für die Volltextsuche ({exc}). Sonderzeichen wie "
            f'" ( ) * NEAR müssen der FTS5-Syntax folgen — mit --literal stattdessen '
            f"wörtlich (als Teilstring) suchen."
        )

    if not rows:
        print("Keine Treffer.")
        return 0

    if args.json:
        results = []
        for row in rows:
            d = dict(row)
            if args.context:
                chat_id_row = conn.execute(
                    "SELECT chat_id FROM messages WHERE id = ?", (row["id"],)
                ).fetchone()
                if chat_id_row:
                    before, after = _context_rows(
                        conn, chat_id_row["chat_id"], row["sent_at"], row["id"], args.context
                    )
                    d["context_before"] = [dict(r) for r in before]
                    d["context_after"] = [dict(r) for r in after]
            results.append(d)
        _print_json(results)
        return 0

    for row in rows:
        stamp = _fmt_ts(row["sent_at"])
        snippet = (row["snippet"] or row["body"] or "").replace("\n", " ")
        print(f"{stamp}  [{row['chat']}] {row['author']}: {snippet}")
        if args.context:
            _print_context(conn, row, args.context)
    return 0


def cmd_show(args, conn) -> int:
    backup = _resolve_backup(args, conn, required_context=True)
    chat_row = _resolve_chat(conn, args.chat, backup)
    filters = _collect_filters(args, backup=backup)
    # Exakt über die Zeilen-ID filtern, nicht über den Namen: Chatnamen sind nicht
    # eindeutig und ein Name kann Teilstring eines anderen sein.
    filters.pop("chat", None)
    filters["chat_id"] = chat_row["chat_id"]
    plain = args.format != "md"

    if args.tail:
        where, params = query.build_filter(conn, **filters)
        total = conn.execute(f"SELECT COUNT(*) AS n {query.BASE} {where}", params).fetchone()["n"]
        offset = max(0, total - args.tail)
        text = render_transcript(conn, plain=plain, offset=offset, limit=args.tail, ascending=True, **filters)
    elif args.limit:
        text = render_transcript(conn, plain=plain, limit=args.limit, ascending=True, **filters)
    else:
        text = render_transcript(conn, plain=plain, **filters)
    print(text, end="")
    return 0


# ------------------------------------------------------------------ stats


def cmd_stats(args, conn) -> int:
    backup = _resolve_backup(args, conn, required_context=True)
    filters = _collect_filters(args, backup=backup)
    data = query.stats(conn, **filters)

    if args.json:
        _print_json(data)
        return 0

    overall = data["overall"]
    print("=== Gesamt ===")
    print(f"Nachrichten:  {overall['n'] or 0}")
    print(f"  gesendet:   {overall['sent'] or 0}")
    print(f"  empfangen:  {overall['received'] or 0}")
    print(f"Anhänge:      {overall['attachments'] or 0}")
    if overall["first"] and overall["last"]:
        print(f"Zeitraum:     {_fmt_ts(overall['first'])} bis {_fmt_ts(overall['last'])}")
    print()

    if data["by_author"]:
        print("=== Nach Person ===")
        print(format_table(
            ["Person", "Nachrichten", "Zeichen", "Anhänge"],
            [[r["author"], r["n"], r["chars"] or 0, r["attachments"] or 0] for r in data["by_author"]],
        ))
        print()

    if data["by_month"]:
        print("=== Verlauf nach Monat ===")
        print(_bar_chart([(r["bucket"], r["n"]) for r in data["by_month"]]))
        print()

    if data["by_hour"]:
        print("=== Aktivität nach Stunde ===")
        print(_bar_chart([(f"{r['bucket']:02d} Uhr", r["n"]) for r in data["by_hour"]]))
        print()

    if data["by_weekday"]:
        print("=== Aktivität nach Wochentag ===")
        weekday_rows = sorted(data["by_weekday"], key=lambda r: (r["bucket"] + 6) % 7)
        print(_bar_chart([(WEEKDAYS[r["bucket"]], r["n"]) for r in weekday_rows]))
        print()

    if data["media"]:
        print("=== Medientypen ===")
        print(format_table(
            ["Typ", "Anzahl", "Größe", "lokal vorhanden"],
            [[r["content_type"] or "?", r["n"], _human_size(r["bytes"]), r["local"] or 0]
             for r in data["media"]],
        ))
        print()

    if data["reactions"]:
        print("=== Häufigste Reaktionen ===")
        print("  ".join(f"{r['emoji']}×{r['n']}" for r in data["reactions"]))
        print()

    if data["calls"]:
        print("=== Anrufbilanz ===")
        print(format_table(
            ["Art", "Richtung", "Status", "Anzahl"],
            [[r["call_type"] or "?", r["direction"] or "?", r["state"] or "?", r["n"]]
             for r in data["calls"]],
        ))
        print()

    if data["response_times"]:
        print("=== Median-Antwortzeit ===")
        print(format_table(
            ["Person", "n", "Median", "Mittelwert"],
            [[r["author"], r["n"], _human_duration(r["median_s"]), _human_duration(r["mean_s"])]
             for r in data["response_times"]],
        ))
        print()

    if data["top_words"]:
        print("=== Häufigste Wörter ===")
        print(", ".join(f"{w} ({n})" for w, n in data["top_words"]))

    return 0


# ------------------------------------------------------------------ media


def cmd_media(args, conn) -> int:
    backup = _resolve_backup(args, conn, required_context=False)
    progress = None if args.quiet else (lambda msg: print(msg, file=sys.stderr))
    result = export_media(
        conn, args.out, backup=backup, chat=args.chat, media_type=args.type,
        mode="copy" if args.copy else "link", group_by_chat=not args.flat, progress=progress,
    )
    if args.json:
        _print_json(dataclasses.asdict(result))
    else:
        print(f"Exportiert:       {result.exported}")
        print(f"Wiederverwendet:  {result.reused}")
        print(f"Fehlend:          {result.missing}")
        print(f"Geschrieben:      {_human_size(result.bytes_written)}")
    return 0


# ------------------------------------------------------------------ export


def _export_json(conn, out_dir: Path, filters: dict) -> list[Path]:
    written = []
    chat_filter = (filters.get("chat") or "").lower()
    for chat in query.list_chats(conn, backup=filters.get("backup")):
        if chat_filter and chat_filter not in (chat["chat"] or "").lower():
            continue
        sub = dict(filters)
        sub["chat"] = chat["chat"]
        rows = query.transcript(conn, **sub)
        ids = [r["id"] for r in rows]
        atts = query.attachments_for(conn, ids)
        reas = query.reactions_for(conn, ids)
        messages = []
        for r in rows:
            stamp = ms_to_local(r["sent_at"])
            messages.append({
                "id": r["id"],
                "sent_at": stamp.isoformat(timespec="seconds") if stamp else None,
                "author": r["author"],
                "direction": r["direction"],
                "kind": r["kind"],
                "body": r["body"],
                "attachments": [
                    {"content_type": a["content_type"], "file_name": a["file_name"],
                     "size": a["size"], "local_path": a["local_path"]}
                    for a in atts.get(r["id"], []) if a["role"] == "body"
                ],
                "reactions": [
                    {"author": x["author"], "emoji": x["emoji"]} for x in reas.get(r["id"], [])
                ],
                "quote": (
                    {"author": r["quote_author"], "text": r["quote_text"]}
                    if r["has_quote"] else None
                ),
            })
        path = out_dir / (safe_filename(chat["chat"], 60) + ".json")
        path.write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(path)
    return written


def _export_csv(conn, out_dir: Path, filters: dict) -> list[Path]:
    written = []
    chat_filter = (filters.get("chat") or "").lower()
    for chat in query.list_chats(conn, backup=filters.get("backup")):
        if chat_filter and chat_filter not in (chat["chat"] or "").lower():
            continue
        sub = dict(filters)
        sub["chat"] = chat["chat"]
        rows = query.transcript(conn, **sub)
        path = out_dir / (safe_filename(chat["chat"], 60) + ".csv")
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["id", "sent_at", "author", "direction", "kind", "body",
                              "n_attachments", "n_reactions"])
            for r in rows:
                stamp = ms_to_local(r["sent_at"])
                writer.writerow([
                    r["id"], stamp.isoformat(timespec="seconds") if stamp else "",
                    r["author"], r["direction"], r["kind"], r["body"] or "",
                    r["n_attachments"], r["n_reactions"],
                ])
        written.append(path)
    return written


def cmd_export(args, conn) -> int:
    backup = _resolve_backup(args, conn, required_context=True)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    filters = {}
    for key in ("chat", "since", "until"):
        val = getattr(args, key, None)
        if val:
            filters[key] = val
    if backup:
        filters["backup"] = backup
    progress = None if args.quiet else (lambda msg: print(msg, file=sys.stderr))

    if args.format == "html":
        try:
            from .render.html import write_html
        except ImportError as exc:
            raise CliError(
                f"HTML-Export ist noch nicht verfügbar (chappe.render.html fehlt "
                f"oder ist unvollständig): {exc}"
            )
        written = write_html(conn, out, media=args.media, **filters)
    elif args.format == "md":
        written = write_markdown(conn, out, plain=False, **filters)
    elif args.format == "txt":
        written = write_markdown(conn, out, plain=True, **filters)
    elif args.format == "json":
        written = _export_json(conn, out, filters)
    elif args.format == "csv":
        written = _export_csv(conn, out, filters)
    else:
        raise CliError(f"Unbekanntes Format: {args.format}")

    if args.media != "none" and args.format != "html":
        result = export_media(
            conn, out / "media", backup=backup, chat=args.chat,
            mode="copy" if args.media == "copy" else "link", progress=progress,
        )
        if not args.quiet:
            print(
                f"Medien: {result.exported} exportiert, {result.reused} bereits vorhanden, "
                f"{result.missing} fehlend", file=sys.stderr,
            )

    if args.json:
        _print_json([str(p) for p in written])
    else:
        print(f"{len(written)} Datei(en) geschrieben nach {out}")
    return 0


# ------------------------------------------------------------------ sql


def _print_schema(conn, as_json: bool) -> None:
    rows = conn.execute(
        "SELECT name, type FROM sqlite_master WHERE type IN ('table','view') "
        "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%_fts%' ORDER BY type, name"
    ).fetchall()
    result = {}
    for r in rows:
        cols = conn.execute(f"PRAGMA table_info('{r['name']}')").fetchall()
        result[r["name"]] = {
            "art": "Tabelle" if r["type"] == "table" else "Sicht",
            "spalten": [c["name"] for c in cols],
        }
    if as_json:
        _print_json(result)
        return
    for name, info in result.items():
        print(f"{info['art']:8} {name}")
        print("         " + ", ".join(info["spalten"]))
        print()


def cmd_sql(args, conn) -> int:
    if not args.statement:
        _print_schema(conn, args.json)
        return 0
    stmt = args.statement.strip()
    first_word = stmt.split(None, 1)[0].upper() if stmt else ""
    if first_word not in ("SELECT", "WITH", "EXPLAIN"):
        raise CliError(
            "Nur lesende Abfragen sind erlaubt — das Statement muss mit SELECT, WITH "
            "oder EXPLAIN beginnen."
        )
    conn.execute("PRAGMA query_only = ON")
    try:
        rows = conn.execute(stmt).fetchall()
    except sqlite3.Error as exc:
        raise CliError(f"SQL-Fehler: {exc}")
    finally:
        conn.execute("PRAGMA query_only = OFF")

    if not rows:
        print("(keine Zeilen)")
        return 0
    headers = list(rows[0].keys())
    if args.json:
        _print_json([dict(r) for r in rows])
    else:
        print(format_table(headers, [[r[h] for h in headers] for r in rows]))
    return 0


# ------------------------------------------------------------------ info


def cmd_info(args, conn) -> int:
    rows = conn.execute(
        """SELECT label, imported_at, app_version, format_version, media_files_total,
                  media_files_bound
           FROM backups ORDER BY imported_at"""
    ).fetchall()
    if not rows:
        print("[]" if args.json else "Keine Backups importiert.")
        return 0
    if args.json:
        _print_json([dict(r) for r in rows])
        return 0
    headers = ["Label", "Importiert", "App-Version", "Format", "Mediendateien", "davon zugeordnet"]
    table_rows = [
        [r["label"], r["imported_at"], r["app_version"] or "?", r["format_version"] or "?",
         r["media_files_total"], r["media_files_bound"]]
        for r in rows
    ]
    print(format_table(headers, table_rows))
    return 0


# ------------------------------------------------------------------ Parser


def _default_db_path() -> str:
    return os.environ.get("CHAPPE_DB") or "chappe.db"


def build_parser() -> argparse.ArgumentParser:
    globalp = argparse.ArgumentParser(add_help=False)
    globalp.add_argument(
        "--db", default=None,
        help="Pfad zur SQLite-Datenbank (Default: $CHAPPE_DB oder ./chappe.db)",
    )
    globalp.add_argument(
        "--json", action="store_true", help="Maschinenlesbare Ausgabe als JSON statt Tabelle"
    )
    globalp.add_argument(
        "-q", "--quiet", action="store_true", help="Fortschrittsmeldungen unterdrücken"
    )
    globalp.add_argument(
        "--config", default=None,
        help="Pfad zur Quellen-Konfiguration (Default: $XDG_CONFIG_HOME/chappe/sources.json)",
    )

    parser = argparse.ArgumentParser(
        prog="chappe",
        description="Signal-Backups importieren, durchsuchen und auswerten.",
        parents=[globalp],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_filter_args(p, *, author=True):
        p.add_argument("--chat", help="Nur Chats, deren Name diesen Text enthält")
        if author:
            p.add_argument("--author", help="Nur Nachrichten dieser Person (Teilstring des Namens)")
        p.add_argument("--since", help="Nur ab diesem Datum, z. B. 2026-03-14, 2026-03 oder 2026")
        p.add_argument("--until", help="Nur bis zu diesem Datum (ausschließlich)")

    # import
    p_import = sub.add_parser(
        "import", help="Signal-Backup-Verzeichnis importieren",
        description="Liest ein Signal-Backup (main.jsonl + files/) in die Datenbank ein.",
    )
    p_import.add_argument(
        "backup_dir", nargs="?",
        help="Pfad zum Backup-Verzeichnis (enthält main.jsonl und files/); ohne Angabe wird "
             "aus registrierten Quellen gewählt",
    )
    p_import.add_argument("--label", help="Name, unter dem das Backup gespeichert wird")
    p_import.add_argument("--replace", action="store_true", help="Vorhandenes Backup mit gleichem Label ersetzen")
    p_import.add_argument(
        "--keep-secrets", action="store_true",
        help="Schlüsselmaterial und SVR-PIN ungeschützt mit in die Datenbank übernehmen",
    )
    p_import.add_argument("--media", metavar="DIR", help="Nach dem Import Medien direkt in dieses Verzeichnis exportieren")
    p_import.add_argument(
        "--all-sources", action="store_true", help="Alle registrierten Quellen nacheinander importieren"
    )

    # sources
    p_sources = sub.add_parser(
        "sources", help="Verwaltete Backup-Quellen auflisten/verwalten",
    )
    sources_sub = p_sources.add_subparsers(dest="sources_command")
    p_src_add = sources_sub.add_parser("add", help="Quelle registrieren")
    p_src_add.add_argument("path", help="Pfad zum Backup-Verzeichnis (enthält main.jsonl)")
    p_src_add.add_argument("--name", help="Name der Quelle (Default: Verzeichnisname)")
    p_src_remove = sources_sub.add_parser("remove", help="Quelle entfernen")
    p_src_remove.add_argument("name", help="Name der zu entfernenden Quelle")
    p_src_scan = sources_sub.add_parser(
        "scan", help="Verzeichnis nach Signal-Exporten durchsuchen"
    )
    p_src_scan.add_argument("directory", help="Verzeichnis, dessen Unterordner durchsucht werden")

    # chats
    p_chats = sub.add_parser("chats", help="Übersicht aller Chats")
    p_chats.add_argument("--backup", help="Nur dieses Backup (Label, siehe 'info')")

    # search
    p_search = sub.add_parser("search", help="Volltextsuche über Nachrichtentexte")
    p_search.add_argument(
        "query", help='Suchbegriff (FTS5-Syntax: Phrasen in "…", OR, NEAR, Präfix mit *)'
    )
    add_filter_args(p_search)
    p_search.add_argument("--backup", help="Nur dieses Backup (Label, siehe 'info')")
    p_search.add_argument("--limit", type=int, default=50, help="Maximale Trefferzahl (Default: 50)")
    p_search.add_argument(
        "--literal", action="store_true",
        help="Wörtliche Teilstringsuche statt Volltextsuche (nutzt LIKE, kein FTS5)",
    )
    p_search.add_argument(
        "--context", type=int, metavar="N",
        help="N Nachrichten davor/danach aus demselben Chat mitausgeben",
    )

    # show
    p_show = sub.add_parser("show", help="Chatverlauf ausgeben")
    add_filter_args(p_show)
    p_show.add_argument("--backup", help="Backup-Label (bei mehreren Backups erforderlich)")
    p_show.add_argument(
        "--all-backups", action="store_true",
        help="Auswahl bewusst überspringen und über alle Backups gehen (überlappend)",
    )
    p_show.add_argument("--limit", type=int, default=None, help="Höchstens so viele Nachrichten")
    p_show.add_argument("--tail", type=int, metavar="N", help="Nur die letzten N Nachrichten")
    p_show.add_argument(
        "--format", choices=["md", "txt"], default="txt",
        help="Ausgabeformat (Default: txt)",
    )

    # stats
    p_stats = sub.add_parser("stats", help="Statistiken zu Chats, Aktivität und Wörtern")
    add_filter_args(p_stats)
    p_stats.add_argument("--backup", help="Backup-Label (bei mehreren Backups erforderlich)")
    p_stats.add_argument(
        "--all-backups", action="store_true",
        help="Auswahl bewusst überspringen und über alle Backups gehen (überlappend)",
    )

    # media
    p_media = sub.add_parser("media", help="Anhänge mit sprechenden Namen exportieren")
    p_media.add_argument("--out", required=True, metavar="DIR", help="Zielverzeichnis")
    p_media.add_argument("--backup", help="Nur dieses Backup (Label, siehe 'info')")
    p_media.add_argument("--chat", help="Nur Anhänge aus Chats, deren Name diesen Text enthält")
    p_media.add_argument(
        "--type", metavar="TYP",
        help="Nur Anhänge, deren Content-Type mit diesem Präfix beginnt, z. B. image",
    )
    p_media.add_argument("--copy", action="store_true", help="Dateien kopieren statt Hardlinks anzulegen")
    p_media.add_argument("--flat", action="store_true", help="Nicht nach Chat gruppieren")

    # export
    p_export = sub.add_parser("export", help="Chats in ein Dateiformat exportieren")
    p_export.add_argument("format", choices=["html", "md", "txt", "json", "csv"], help="Zielformat")
    p_export.add_argument("--out", required=True, metavar="DIR", help="Zielverzeichnis")
    p_export.add_argument("--backup", help="Backup-Label (bei mehreren Backups erforderlich)")
    p_export.add_argument(
        "--all-backups", action="store_true",
        help="Auswahl bewusst überspringen und über alle Backups gehen (überlappend)",
    )
    p_export.add_argument("--chat", help="Nur Chats, deren Name diesen Text enthält")
    p_export.add_argument("--since", help="Nur ab diesem Datum")
    p_export.add_argument("--until", help="Nur bis zu diesem Datum (ausschließlich)")
    p_export.add_argument(
        "--media", choices=["link", "copy", "none"], default="none",
        help="Anhänge zusätzlich in ein media/-Unterverzeichnis exportieren (Default: none)",
    )

    # sql
    p_sql = sub.add_parser(
        "sql", help="Freie Lese-Abfrage gegen die Datenbank (nur SELECT/WITH/EXPLAIN)",
    )
    p_sql.add_argument(
        "statement", nargs="?",
        help="SQL-Statement; ohne Angabe werden Tabellen und Views mit ihren Spalten aufgelistet",
    )

    # info
    sub.add_parser("info", help="Welche Backups sind importiert")

    return parser


_HANDLERS = {
    "import": cmd_import,
    "sources": cmd_sources,
    "chats": cmd_chats,
    "search": cmd_search,
    "show": cmd_show,
    "stats": cmd_stats,
    "media": cmd_media,
    "export": cmd_export,
    "sql": cmd_sql,
    "info": cmd_info,
}


def _split_global_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    """Löst --db/--json/-q/--quiet/--config aus argv heraus, unabhängig von ihrer
    Position (vor oder nach dem Subcommand).

    Grund: Ein Subparser, der dieselben Optionen über `parents=` erneut definiert,
    setzt sie beim Parsen auf ihren Default zurück und überschreibt damit einen
    schon vom übergeordneten Parser gesetzten Wert (bekannte argparse-Eigenart).
    Deshalb werden diese vier Optionen nur hier, vor dem eigentlichen Parsen,
    erkannt und aus argv entfernt.
    """
    g = argparse.ArgumentParser(add_help=False)
    g.add_argument("--db", default=None)
    g.add_argument("--json", action="store_true")
    g.add_argument("-q", "--quiet", action="store_true")
    g.add_argument("--config", default=None)
    return g.parse_known_args(argv)


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    global_ns, remaining = _split_global_args(raw)
    parser = build_parser()
    args = parser.parse_args(remaining)
    args.db = global_ns.db
    args.json = global_ns.json
    args.quiet = global_ns.quiet
    args.config = global_ns.config
    conn = None
    try:
        db_path = args.db or _default_db_path()
        conn = connect(db_path)
        return _HANDLERS[args.command](args, conn)
    except KeyboardInterrupt:
        print("\nAbgebrochen.", file=sys.stderr)
        return 130
    except CliError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
    except sqlite3.Error as exc:
        print(f"Datenbankfehler: {exc}", file=sys.stderr)
        return 1
    finally:
        if conn is not None:
            conn.close()
