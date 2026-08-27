"""RPC-Adapter für den Electron-Sidecar: NDJSON über stdin/stdout.

Implementiert den Protokollvertrag aus `docs/gui-plan.md` (Slice 0). Jede
Zeile auf stdin ist eine Anfrage, jede Zeile auf stdout eine Antwort — beides
kompaktes JSON ohne Pretty-Print, UTF-8. stderr bleibt der Diagnose
vorbehalten und ist nie Teil des Protokolls.

Diese Datei ruft `query.py` direkt auf und parst niemals die Textausgabe der
CLI (`cli.py`) — das steht in `docs/gui-plan.md` unter "Nicht verhandelbar".
Wer das umgeht, koppelt die App an Formatierungsentscheidungen der
Kommandozeile und bricht sie mit jedem Feinschliff dort.
"""

from __future__ import annotations

import io
import json
import sqlite3
import sys
from typing import Any, Callable, TextIO

from . import __version__, query

#: Version des RPC-Protokolls selbst (nicht die von chappe). Steigt, sobald
#: sich Nachrichtenform oder Methodenbedeutung ändern, nicht bei neuen Methoden.
PROTOCOL_VERSION = 1


def _write(writer: TextIO, obj: dict[str, Any]) -> None:
    """Schreibt genau eine NDJSON-Zeile: kompakt, UTF-8, mit '\\n' abgeschlossen."""
    writer.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
    writer.write("\n")
    writer.flush()


def _ok(request_id: Any, result: Any) -> dict[str, Any]:
    return {"id": request_id, "ok": True, "result": result}


def _error(request_id: Any, code: str, message: str) -> dict[str, Any]:
    return {"id": request_id, "ok": False, "error": {"code": code, "message": message}}


def _rows_to_list(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


# ------------------------------------------------------------------ Methoden


def _handle_ping(_conn: sqlite3.Connection, _params: dict[str, Any]) -> dict[str, Any]:
    return {"version": __version__, "protocol": PROTOCOL_VERSION}


def _handle_list_chats(
    conn: sqlite3.Connection, _params: dict[str, Any]
) -> list[dict[str, Any]]:
    """Liste der Chats des geöffneten Backups — ungefiltert, wie `chappe chats`
    ohne --backup. Kein Namensfilter hier (siehe CLAUDE.md, Invariante 2):
    künftige Einschränkung läuft über chat_id oder Backup-Label, nie über
    einen Teilstring auf c.name.
    """
    return _rows_to_list(query.list_chats(conn))


def _handle_shutdown(_conn: sqlite3.Connection, _params: dict[str, Any]) -> dict[str, Any]:
    # Schliesst die Verbindung nicht selbst — das macht serve() erst NACH
    # dem Schreiben der Erfolgsantwort (siehe unten), sonst würde die
    # Antwort auf eine bereits geschlossene Verbindung folgen.
    return {"ok": True}


_METHODS: dict[str, Callable[[sqlite3.Connection, dict[str, Any]], Any]] = {
    "ping": _handle_ping,
    "list_chats": _handle_list_chats,
    "shutdown": _handle_shutdown,
}


# ------------------------------------------------------------------ Schleife


def serve(conn: sqlite3.Connection, reader: TextIO, writer: TextIO) -> int:
    """Protokollschleife: liest Anfragen zeilenweise aus `reader`, schreibt
    Antworten nach `writer`. Läuft bis 'shutdown' oder bis `reader` erschöpft
    ist, und gibt den Exit-Code zurück (0 bei sauberem Ende). Schließt `conn`
    auf jedem Weg hinaus, auch bei einer Ausnahme.

    Als eigene Funktion mit austauschbaren Strömen gehalten, damit sie sich
    ohne Subprozess gegen io.StringIO testen lässt — `run()` ist nur die
    dünne Hülle für den echten Einsatz über stdin/stdout.
    """
    # Die Verbindung wird in jedem Fall geschlossen, nicht nur auf dem
    # "shutdown"-Weg. Der andere Weg ist real: fällt der Elternprozess hart
    # weg, schließt der Kernel die Pipe, das Kind bekommt EOF auf stdin und
    # die Schleife endet, ohne je einen shutdown-Rahmen gesehen zu haben.
    try:
        for raw_line in reader:
            line = raw_line.strip()
            if not line:
                continue  # leere Zeile: kein Absturz, keine Antwort, weiter

            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                _write(writer, _error(None, "bad_request", "Zeile ist kein gültiges JSON."))
                continue

            if not isinstance(request, dict) or "method" not in request:
                request_id = request.get("id") if isinstance(request, dict) else None
                _write(
                    writer,
                    _error(request_id, "bad_request", "Anfrage muss ein Objekt mit 'method' sein."),
                )
                continue

            request_id = request.get("id")
            method = request.get("method")
            params = request.get("params") or {}

            # "method" kommt aus fremdem JSON und ist damit zur Laufzeit beliebig
            # typisiert (z. B. eine Zahl oder null) — ein Methodenname, der keine
            # Zeichenkette ist, kann nie in _METHODS stehen und ist ebenfalls
            # schlicht eine unbekannte Methode, kein eigener Fehlerfall.
            handler = _METHODS.get(method) if isinstance(method, str) else None
            if handler is None:
                _write(
                    writer,
                    _error(request_id, "unknown_method", f"Unbekannte Methode: {method!r}"),
                )
                continue

            try:
                result = handler(conn, params)
            except Exception as exc:  # Schleife darf nie abstürzen, siehe Protokollvertrag
                _write(writer, _error(request_id, "internal_error", str(exc)))
                continue

            _write(writer, _ok(request_id, result))

            if method == "shutdown":
                return 0

        return 0
    finally:
        conn.close()


def run(conn: sqlite3.Connection) -> int:
    """Öffnet das Protokoll auf den echten stdin/stdout und läuft bis
    'shutdown'. Erzwingt UTF-8 auf beiden Strömen, unabhängig von der
    Konsolen-Codepage — auf Windows ist das kein Detail, sonst brechen
    Umlaute in Chatnamen still, statt eine Fehlermeldung zu zeigen.
    """
    # sys.stdin/sys.stdout sind laut typeshed nur als das generische TextIO-
    # Protokoll typisiert, das kein reconfigure() kennt — an der Laufzeit
    # sind es aber praktisch immer io.TextIOWrapper-Instanzen, die es haben.
    # isinstance() statt hasattr() engt den Typ für basedpyright sauber ein,
    # ohne ein pauschales type: ignore.
    for stream in (sys.stdin, sys.stdout):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", newline="\n")
    return serve(conn, sys.stdin, sys.stdout)
