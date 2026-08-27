# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller-Spec für den Electron-Sidecar aus dem GUI-Durchstich (Slice 0).

Bündelt `src/chappe` (das komplette Paket, nicht nur das RPC-Subkommando) zu
einem eigenständigen Binary namens ``chappe-rpc``, das Electron als Kindprozess
startet und über das NDJSON-Protokoll aus dem Auftrag befragt (`ping`,
`list_chats`, `shutdown`).

Modus **--onedir**, nicht --onefile. Begründung (siehe docs/research/sidecar.md
und docs/research/build.md): kein Selbst-Entpack-Overhead bei jedem
Prozessstart, kein Bootloader-Zweitprozess (der bei --onefile das saubere
Beenden über einfaches child.kill() erschwert), spürbar weniger
Virenscanner-Fehlalarme, und robuster bei der späteren macOS-Notarisierung
(Slice 11).

`schema.sql` MUSS mit ins Bundle, weil `importer.py` es zur Laufzeit über
``Path(__file__).with_name("schema.sql")`` sucht (siehe importer.py, Zeile
mit `SCHEMA = Path(__file__).with_name("schema.sql")`). Der Bootloader setzt
`__file__` für ein eingefrorenes Modul `chappe.importer` auf einen Pfad, der
die Paketstruktur spiegelt (`<_MEIPASS>/chappe/importer.py`) — deshalb muss
schema.sql im Bundle exakt unter `chappe/schema.sql` liegen, nicht daneben
oder im Wurzelverzeichnis. Der `datas`-Eintrag unten trägt das über die
`.spec`-Datei ein, bewusst nicht über `--add-data` auf der Kommandozeile: der
Trenner in `--add-data quelle:ziel` ist auf Windows ein Semikolon und sonst
ein Doppelpunkt, und diese Fallunterscheidung gehört nicht dreimal in ein
CI-YAML (Slice 1). Eine `.spec`-Datei mit `datas=[...]` ist plattform-
unabhängig und damit die robustere, wartbarere Lösung.

Aufruf (siehe packaging/README.md für Details):

    pyinstaller packaging/chappe.spec --distpath dist --workpath build/work

SPECPATH/workpath sind von PyInstaller in diese Datei injizierte Namen:
SPECPATH ist das Verzeichnis, in dem diese `.spec`-Datei liegt (also
`packaging/`); workpath ist der aufgelöste --workpath-Wert. Alle festen Pfade
unten werden relativ zu SPECPATH aufgelöst, damit der Aufruf unabhängig vom
aktuellen Arbeitsverzeichnis funktioniert.
"""

import os

REPO_ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))  # noqa: F821 (SPECPATH kommt von PyInstaller)
SRC_DIR = os.path.join(REPO_ROOT, "src")
SCHEMA_SQL = os.path.join(SRC_DIR, "chappe", "schema.sql")

BINARY_NAME = "chappe-rpc"

# `src/chappe/__main__.py` lässt sich nicht direkt als PyInstaller-Skript
# analysieren: es enthält `from .cli import main` (relativer Import), und der
# eingefrorene Lauf startet das Einstiegsskript als Modul "__main__" *ohne*
# Paketkontext — zur Laufzeit dann "ImportError: attempted relative import
# with no known parent package" (per Testbuild geprüft, kein theoretisches
# Risiko). `__main__.py` selbst ändern ist nicht erlaubt (liegt unter src/,
# außerhalb des Auftragsumfangs dieser .spec-Datei), und ein zusätzliches
# Bootstrap-Skript als eigene Projektdatei ebenfalls nicht (Auftrag erlaubt
# ausschließlich chappe.spec und README.md unter build/).
#
# Deshalb erzeugt dieser Block bei jeder Auswertung der .spec-Datei einen
# minimalen Bootstrap in `workpath` — reines, bei jedem Lauf neu geschriebenes
# Build-Artefakt wie PyInstallers eigene Zwischendateien dort, kein
# Projekt-Quellcode. Er importiert `chappe.cli.main` absolut, exakt der
# gleiche Einstiegspunkt, den auch `[project.scripts]` in pyproject.toml
# (`chappe = "chappe.cli:main"`) für den installierten Fall nutzt.
os.makedirs(workpath, exist_ok=True)  # noqa: F821 (workpath kommt von PyInstaller)
ENTRY_SCRIPT = os.path.join(workpath, "_chappe_rpc_entry.py")  # noqa: F821
with open(ENTRY_SCRIPT, "w", encoding="utf-8") as _entry_fh:
    _entry_fh.write(
        "# Automatisch von build/chappe.spec erzeugt -- nicht von Hand pflegen.\n"
        "import sys\n"
        "from chappe.cli import main\n"
        "\n"
        "if __name__ == \"__main__\":\n"
        "    sys.exit(main())\n"
    )

a = Analysis(  # noqa: F821 (von PyInstaller zur Ausführungszeit bereitgestellt)
    [ENTRY_SCRIPT],
    pathex=[SRC_DIR],
    binaries=[],
    # Ziel "chappe" spiegelt die Paketstruktur (chappe/schema.sql), siehe
    # Erklärung oben. Reine Standardbibliothek (pyproject.toml: dependencies
    # = []) — hiddenimports/Hooks für Drittanbieter-Pakete sind nicht nötig.
    datas=[
        (SCHEMA_SQL, "chappe"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=BINARY_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # console=True ist Pflicht, nicht Default-Beibehaltung: `chappe-rpc`
    # kommuniziert über stdin/stdout (NDJSON-Protokoll). --windowed/--noconsole
    # setzt sys.stdout/sys.stderr auf None und würde jeden print()/Logging-
    # Aufruf zum Absturz bringen (docs/research/sidecar.md, Abschnitt 1).
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    # upx=False: dieselbe Antivirus-Vorsicht wie bei der --onedir-Wahl.
    # Gepackte/komprimierte Binaries lösen bei Heuristik-Scannern zusätzlich
    # häufiger Fehlalarme aus als unkomprimierte.
    upx=False,
    upx_exclude=[],
    name=BINARY_NAME,
)
