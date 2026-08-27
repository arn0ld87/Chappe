"""Tests für chappe.rpc — die NDJSON-Protokollschleife des Electron-Sidecars.

Läuft direkt gegen `rpc.serve()` mit io.StringIO statt über einen
Subprozess — Vertrag und Rationale in docs/gui-plan.md ("Nicht
verhandelbar"): kein Text-Parsing der CLI, und die Testsuite bleibt unter
einer Sekunde.
"""

from __future__ import annotations

import io
import json
import sqlite3

import pytest

import chappe
from chappe import query, rpc


def _run(conn: sqlite3.Connection, requests: list) -> tuple[list[dict], int]:
    """Schickt jede Zeile aus `requests` (dict -> JSON, str -> wörtlich) durch
    `rpc.serve()` und gibt (Antworten, Exit-Code) zurück."""
    payload = (
        "\n".join(
            line if isinstance(line, str) else json.dumps(line, ensure_ascii=False)
            for line in requests
        )
        + "\n"
    )
    reader = io.StringIO(payload)
    writer = io.StringIO()
    exit_code = rpc.serve(conn, reader, writer)
    out_lines = [ln for ln in writer.getvalue().splitlines() if ln]
    # Jede Zeile muss für sich gültiges, kompaktes JSON ohne Pretty-Print sein.
    for ln in out_lines:
        assert "\n" not in ln
    return [json.loads(ln) for ln in out_lines], exit_code


# --------------------------------------------------------------------- ping


def test_ping_returns_version_and_protocol(db):
    conn, _ = db
    responses, exit_code = _run(conn, [{"id": 1, "method": "ping"}])
    assert exit_code == 0
    assert responses == [
        {
            "id": 1,
            "ok": True,
            "result": {"version": chappe.__version__, "protocol": rpc.PROTOCOL_VERSION},
        }
    ]


# ---------------------------------------------------------------- list_chats


def test_list_chats_matches_query_module(db):
    conn, _ = db
    expected = [dict(row) for row in query.list_chats(conn)]
    assert expected, "Fixture muss mindestens einen Chat mit Nachrichten liefern"

    responses, exit_code = _run(conn, [{"id": 2, "method": "list_chats"}])
    assert exit_code == 0
    assert len(responses) == 1
    assert responses[0]["id"] == 2
    assert responses[0]["ok"] is True
    assert responses[0]["result"] == expected


# ------------------------------------------------------------ unknown_method


def test_unknown_method_returns_error_and_loop_continues(db):
    conn, _ = db
    responses, exit_code = _run(
        conn,
        [
            {"id": 3, "method": "does_not_exist"},
            {"id": 4, "method": "ping"},
        ],
    )
    assert exit_code == 0
    assert len(responses) == 2

    first = responses[0]
    assert first["id"] == 3
    assert first["ok"] is False
    assert first["error"]["code"] == "unknown_method"
    assert isinstance(first["error"]["message"], str) and first["error"]["message"]

    # Die Schleife läuft nach dem Fehler ganz normal weiter.
    assert responses[1]["id"] == 4
    assert responses[1]["ok"] is True


# ------------------------------------------------------------------- Parsen


def test_malformed_json_returns_bad_request_with_null_id_and_continues(db):
    conn, _ = db
    responses, exit_code = _run(
        conn,
        [
            "{das ist kein JSON",
            {"id": 5, "method": "ping"},
        ],
    )
    assert exit_code == 0
    assert len(responses) == 2

    first = responses[0]
    assert first["id"] is None
    assert first["ok"] is False
    assert first["error"]["code"] == "bad_request"

    # Kein Absturz — die nächste, gültige Anfrage wird ganz normal beantwortet.
    assert responses[1]["id"] == 5
    assert responses[1]["ok"] is True


def test_request_without_method_is_also_bad_request(db):
    conn, _ = db
    responses, exit_code = _run(conn, [{"id": 6, "params": {}}])
    assert exit_code == 0
    assert responses[0]["ok"] is False
    assert responses[0]["error"]["code"] == "bad_request"


# ----------------------------------------------------------------- shutdown


def test_shutdown_closes_database_and_ends_loop_cleanly(db):
    conn, _ = db
    responses, exit_code = _run(
        conn,
        [
            {"id": 7, "method": "shutdown"},
            # Diese Zeile darf nicht mehr verarbeitet werden — die Schleife
            # endet mit shutdown, bevor sie erneut liest.
            {"id": 8, "method": "ping"},
        ],
    )
    assert exit_code == 0
    assert len(responses) == 1
    assert responses[0] == {"id": 7, "ok": True, "result": {"ok": True}}

    # Die Datenbankverbindung ist tatsächlich geschlossen, nicht nur dem Namen nach.
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")
