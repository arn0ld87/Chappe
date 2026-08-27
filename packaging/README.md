# Lokal bauen: `chappe-rpc`

Baut aus `src/chappe` ein eigenständiges Binary `chappe-rpc` (macOS/Linux)
bzw. `chappe-rpc.exe` (Windows) — den Sidecar-Prozess, den Electron als
Kindprozess startet. Modus `--onedir`, kein `--windowed`. Details und
Begründungen stehen als Kommentare in [`chappe.spec`](./chappe.spec); die
zugrunde liegende Recherche in `docs/research/sidecar.md` und
`docs/research/build.md`.

Das hier ist der lokale Handbau für Slice 0. Ein GitHub-Actions-Workflow für
alle drei Plattformen kommt erst in Slice 1.

## Voraussetzungen

```bash
python3 -m venv .venv && source .venv/bin/activate   # falls noch nicht vorhanden
pip install pyinstaller
```

Getestet gegen PyInstaller 6.22.2 (Stand `docs/research/build.md`). Andere
6.x-Versionen sollten funktionieren, da die `.spec`-Datei keine
versionsspezifischen Optionen nutzt.

## Bauen

Vom Repo-Wurzelverzeichnis aus:

```bash
pyinstaller packaging/chappe.spec --distpath dist --workpath build/work
```

**Warum Quelle und Ausgabe getrennt liegen:** `chappe.spec` und diese
Anleitung sind Quelldateien und liegen deshalb unter `packaging/`. Alles, was
der Build erzeugt, landet unter `build/work/` (Arbeitscache) und `dist/`
(Ergebnis) — beide sind in `.gitignore` ausgeschlossen. Ohne die beiden Flags
legt PyInstaller seine Verzeichnisse im aktuellen Arbeitsverzeichnis an und
vermischt beides wieder.

Ergebnis: `dist/chappe-rpc/` — enthält die ausführbare Datei
(`chappe-rpc` bzw. `chappe-rpc.exe`) direkt im Ordner, alle mitgelieferten
Python-Laufzeit-Dateien und Daten (inklusive `schema.sql`) darunter in
`_internal/`. Electron startet später genau diese ausführbare Datei, nicht
etwas aus `_internal/` direkt.

## Prüfen, dass `schema.sql` im Bundle gelandet ist

Zwei Prüfungen, strukturell und funktional. Die strukturelle zeigt nur, dass
irgendeine Datei mit dem Namen existiert — die funktionale beweist, dass
`chappe` sie zur Laufzeit tatsächlich über
`Path(__file__).with_name("schema.sql")` findet, also genau dort, wo
`importer.py` sie erwartet.

**1. Strukturell** — Datei muss unter `chappe/` liegen, nicht im
Bundle-Wurzelverzeichnis:

```bash
find dist/chappe-rpc -name schema.sql
# erwartet: dist/chappe-rpc/_internal/chappe/schema.sql
```

Liegt sie stattdessen direkt unter `_internal/schema.sql` (ohne
`chappe/`-Unterordner), ist das Ziel im `datas`-Eintrag der `.spec`-Datei
falsch — siehe Kommentar dort, warum "chappe" die Paketstruktur spiegeln
muss.

**2. Funktional** — ein echter Import gegen das synthetische Testbackup, das
`tests/fixtures/make_fixture.py` ohnehin für die Testsuite erzeugt (kein
echtes Backup nötig):

```bash
python3 tests/fixtures/make_fixture.py /tmp/chappe-fixture
./dist/chappe-rpc/chappe-rpc --db /tmp/chappe-smoke.db import /tmp/chappe-fixture --label smoke
```

Schlägt das mit einer Meldung zu einer fehlenden `schema.sql` fehl (statt mit
einer Import-Zusammenfassung durchzulaufen), ist die Datendatei nicht im
Bundle oder liegt am falschen Ort — das Binary selbst startet in dem Fall
trotzdem, weil `chappe` das Schema erst beim tatsächlichen
Datenbank-Aufbau liest, nicht beim Programmstart.

Aufräumen danach: `rm /tmp/chappe-smoke.db` und `rm -r /tmp/chappe-fixture`.

## Warum diese Dateien nicht unter `build/` liegen

Sie lagen dort zunächst — und wurden von der `build/`-Regel in `.gitignore`
verschluckt, die für Python-Build-Artefakte gedacht ist. Zwei Quelldateien in
einem Verzeichnis, dessen Name „Ausgabe" bedeutet, sind eine Falle, die man
nur einmal stellt. Deshalb `packaging/` für die Quellen und `build/` weiterhin
ausschließlich für Erzeugtes.
