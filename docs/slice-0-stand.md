# Slice 0 — Stand

Ergebnis der Integration der drei parallel gebauten Teile (RPC-Python-Seite,
Electron-Gerüst, PyInstaller-Build). Stand: 2026-08-27.

## Läuft es? Ja.

Alle drei Teile sprechen denselben Protokollvertrag. Testsuite grün, Electron-
App zeigt echte Chats aus einer echten (synthetischen) Datenbank, Sidecar
beendet sich sauber — verifiziert, nicht nur gelesen.

## Testsuite

```
PYTHONPATH=src python3 -m pytest tests -q
→ 62 passed in 0.27s
```

56 bestehende + 6 neue (`tests/test_rpc.py`). Keiner rot.

## Wie man die App startet

```bash
npm install   # bereits erledigt, node_modules liegt im Repo

# Ohne echte DB: synthetisches Fixture erzeugen und importieren
PYTHONPATH=src python3 tests/fixtures/make_fixture.py .devdata/fixture
PYTHONPATH=src python3 -m chappe import .devdata/fixture --label devtest \
  --db .devdata/chappe.db

# App im Dev-Modus, gegen genau diese DB
CHAPPE_DB_PATH="$PWD/.devdata/chappe.db" npm run dev
```

Ohne `CHAPPE_DB_PATH` sucht die App unter `<userData>/chappe.db`
(`~/Library/Application Support/Chappe/chappe.db` auf macOS) — für Slice 0
ohne Onboarding-UI der Weg über die Umgebungsvariable.

`.devdata/` ist jetzt in `.gitignore` (synthetisch, aber Signal-Backup-
förmig — gehört trotzdem nicht ins Repo).

## Was verifiziert wurde (nicht nur behauptet)

**IPC-Kette Ende-zu-Ende:** Electron-Hauptprozess startet
`python3 -m chappe rpc --db …`, `ping()` und `listChats()` liefern über
Preload/contextBridge echte Daten aus der SQLite-DB in den Renderer.
Nachgewiesen mit temporärer Diagnose-Ausgabe (`console-message`-Listener in
`main/index.ts`, ein `console.log` in `App.vue`), danach wieder entfernt —
beide Dateien sind im Ist-Zustand unverändert gegenüber dem, was ohne diese
Prüfung dort stünde:

```
SLICE0-VERIFY ping={"version":"0.1.0","protocol":1} chats=1
```

**Sauberes Beenden (Verifikationskriterium von Slice 0):** App per
`osascript -e 'tell application id "com.github.Electron" to quit'` beendet
(entspricht Cmd+Q/normalem Schließen) und mit `ps aux` geprüft:

```
[sidecar] beendet (code=0, signal=null)
```
→ kein Electron-, kein Python-Prozess übrig.

**Renderer-Absturz-Fall:** Renderer-Helper-Prozess hart mit `kill -9`
beendet. `render-process-gone` löste wie vorgesehen `app.quit()` aus, danach
lief derselbe Shutdown-Pfad (`before-quit` → `sidecar.shutdown()`) und der
Python-Prozess endete ebenfalls mit `code=0`. Auch hier: `ps aux` danach
leer.

**PyInstaller-Build nach dem Verschieben nach `packaging/`:** lief durch.
`pyinstaller` war nirgends installiert — in einer temporären `.venv-build`
(pyinstaller 6.22.2, exakt die in `packaging/README.md` genannte Version)
installiert, gebaut, geprüft, danach `.venv-build`, `dist/` und `build/`
wieder entfernt (nichts davon gehört ins Repo).

```
find dist/chappe-rpc -name schema.sql
→ dist/chappe-rpc/_internal/chappe/schema.sql   (korrekt, siehe .spec-Kommentar)

./dist/chappe-rpc/chappe-rpc --db /tmp/… import … --label smoke
→ Import lief durch (Zusammenfassung mit Nachrichten-/Anhangszahlen)

echo '{"id":1,"method":"ping"} …' | ./dist/chappe-rpc/chappe-rpc rpc --db …
→ ping/list_chats/shutdown alle korrekt beantwortet, Exit-Code 0
```

## Gefundene und behobene Fehler

1. **`src/chappe/rpc.py:111`** — `_METHODS.get(method)` bekam `method` mit
   Typ `Unknown | None` (aus fremdem JSON), Parameter verlangt `str`. Fix:
   `isinstance(method, str)`-Guard vor dem Lookup; ein nicht-string-Methodenname
   fällt jetzt korrekt unter `unknown_method` statt einen Typfehler zu
   verstecken.
2. **`src/chappe/rpc.py:139`** — `TextIO.reconfigure()` existiert laut
   typeshed nicht auf dem generischen Protokoll, wohl aber zur Laufzeit auf
   `io.TextIOWrapper` (das, was `sys.stdin`/`sys.stdout` praktisch immer
   sind). Fix: `isinstance(stream, io.TextIOWrapper)` statt `hasattr(...)` —
   engt den Typ für basedpyright sauber ein, kein pauschales
   `type: ignore`.
3. **`src/chappe/cli.py` (`cmd_rpc`, Zeile ~966)** — Parameter `args` war
   unbenutzt. Bleibt aber Pflicht: `_HANDLERS` ruft jeden Befehl einheitlich
   als `(args, conn)` auf. Umbenannt zu `_args`, mit Kommentar, warum er da
   ist, statt still ungenutzt herumzuliegen.
4. **`src/chappe/rpc.py`** (`_handle_ping`, `_handle_list_chats`,
   `_handle_shutdown`) — `conn`/`params` teils unbenutzt, aus demselben
   Signaturzwang (`_METHODS: dict[str, Callable[[sqlite3.Connection,
   dict[str, Any]], Any]]`). Ebenfalls mit `_`-Präfix sichtbar gemacht statt
   entfernt — Entfernen würde die einheitliche Handler-Signatur brechen.
5. **`app/main/paths.ts`** — Namensbruch zwischen den beiden parallel
   gebauten Teilen: `packaging/chappe.spec` erzeugt ein Binary namens
   `chappe-rpc` (`chappe-rpc.exe` unter Windows), `resolveSidecarCommand()`
   suchte im gepackten Zustand aber nach `chappe`/`chappe.exe`. In Slice 0
   noch folgenlos (es gibt noch kein `extraResources`, das den gepackten
   Pfad überhaupt auslöst), hätte aber in Slice 1 den Produktionsstart der
   App stillschweigend brechen lassen. Fix: Binärname an `BINARY_NAME` in
   der `.spec`-Datei angeglichen.

Basedpyright zeigt danach für `rpc.py` und `cli.py`: **0 Fehler.**

## basedpyright — Rest des Projekts (nicht Teil dieses Slices)

`basedpyright` über das Gesamtprojekt findet weitere Fehler in `importer.py`,
`model.py` und `render/html.py` (durchweg `int | None` an Stellen, die `int`
erwarten, und ein `Callable`-Typmismatch) sowie in `tests/*.py` (fehlende
`pytest`-/`make_fixture`-Auflösung, weil basedpyright hier ohne die
Projekt-`.venv` läuft, und ein paar `TypedDict`-Zugriffsmuster in
`test_model.py`, die basedpyright falsch als Slice-Syntax liest). Laut
Auftrag ausdrücklich nicht anfassen — steht hier nur, damit es nicht als
„übersehen" durchgeht. Alles vorbestehend, keins davon durch die drei
Slice-0-Teile verursacht.

## Offene Reste / was beim nächsten Mal zuerst dran ist

- **`.venv` fürs Projekt fehlt.** `pytest` läuft nur über System-`python3`,
  nicht über eine projekteigene virtuelle Umgebung — deshalb auch
  basedpyrights `reportMissingImports` auf `pytest` in den Test-Dateien.
  Sollte eingerichtet werden, bevor CI (Slice 1) das braucht.
- **PyInstaller ist an keiner Stelle im Projekt referenziert** (nicht in
  `pyproject.toml`, keine dev-Dependency-Gruppe) — konsistent mit „Paket
  chappe hat null Laufzeit-Abhängigkeiten", aber jeder, der den Build lokal
  wiederholen will, muss `packaging/README.md` lesen und selbst eine venv
  aufsetzen. Für Slice 1 (CI) muss diese Installation reproduzierbar
  gepinnt werden (6.22.2, siehe README).
- **`electron-vite dev` startet zweimal denselben Vite-Port-Konflikt**, wenn
  ein vorheriger Lauf nicht sauber beendet wurde (`Port 5173 is in use`) —
  kein Bug der App, sondern ein Betriebsdetail für die lokale Entwicklung:
  vor jedem `npm run dev` prüfen, ob ein alter `electron-vite`-Prozess noch
  läuft.
- **`app/main/index.ts`s `console-message`-Handler-Signatur** (falls künftig
  für echtes Renderer-Logging wieder eingebaut) ist laut Electron 44 als
  `(event, level, message, line, sourceId)` deprecated zugunsten eines
  einzelnen Event-Objekts — bei Gelegenheit auf die neue Form heben, aktuell
  nicht im Code (nur während dieser Verifikation kurz probeweise drin,
  danach entfernt).
- **Kein Onboarding/keine DB-Auswahl-UI** — erwartet für Slice 0, kommt laut
  `docs/gui-plan.md` in Slice 4. Bis dahin bleibt `CHAPPE_DB_PATH` der Weg
  für einen manuellen Testlauf.
- **`app/main/paths.ts`-Fix (Punkt 5 oben) ist ungetestet gegen einen echten
  gepackten Build**, weil `electron-builder`/`extraResources` erst in
  Slice 1 kommt. Beim Bau von Slice 1 gezielt prüfen, dass
  `process.resourcesPath/sidecar/chappe-rpc` tatsächlich existiert und
  ausführbar ist.

## Nicht angefasst (wie im Auftrag verlangt)

`query.py`, `importer.py`, `media.py`, `model.py`, bestehende Tests — keine
Änderungen. Ihre basedpyright-Befunde stehen oben nur zur Dokumentation.
