"""Gemeinsame Pytest-Fixtures für chappe."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# tests/fixtures/make_fixture.py ist kein Paket — Verzeichnis direkt auf den
# Suchpfad legen, damit sowohl Tests als auch das Skript selbst importieren können.
sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from make_fixture import build_fixture  # noqa: E402

from chappe import importer  # noqa: E402


@pytest.fixture(scope="session")
def fixture_backup(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Baut das künstliche Mini-Backup einmal pro Testsession."""
    target = tmp_path_factory.mktemp("backup") / "signal-export-test"
    return build_fixture(target)


@pytest.fixture
def db(fixture_backup: Path, tmp_path: Path):
    """Frische SQLite-DB mit importiertem Mini-Backup, für jeden Test neu angelegt."""
    conn = importer.connect(tmp_path / "vault.db")
    report = importer.import_backup(conn, fixture_backup, label="test")
    yield conn, report
    conn.close()
