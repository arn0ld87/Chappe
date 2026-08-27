# PyInstaller-Binary als Electron-Sidecar

Recherchestand: 2026-08-26. Bezug: `docs/gui-plan.md` Slice 0/1 (Durchstich,
Drei-Plattform-Beweis). Ziel dieser Datei ist die Beantwortung von vier
konkreten Fragen aus dem Plan, nicht ein allgemeiner Sidecar-Leitfaden.

## Abriss

Electron und PyInstaller haben beide keine gemeinsame, offizielle
Sidecar-Anleitung — das Muster ist Community-Konvention, kein dokumentiertes
Feature. Es setzt sich aus vier unabhängigen Bausteinen zusammen:

1. **Build**: `pyinstaller` erzeugt je Plattform ein eigenständiges Binary
   (`--onefile` oder `--onedir`), das Chappe inklusive `schema.sql` und ohne
   Python-Interpreter-Abhängigkeit enthält.
2. **Packaging**: `electron-builder` kopiert dieses Binary über
   `extraResources` unverändert (nicht ASAR-gepackt) ins Ressourcenverzeichnis
   des App-Bundles — plattformabhängig ein anderer Pfad.
3. **Laufzeit-Auflösung**: Der Electron-Hauptprozess unterscheidet
   Entwicklung/Produktion über `app.isPackaged` und baut den Pfad zum Binary
   aus `process.resourcesPath` zusammen.
4. **Prozessführung**: `child_process.spawn()` startet das Binary, ein
   zeilenbasiertes JSON-Protokoll läuft über `stdin`/`stdout`, und der
   Hauptprozess muss den Kindprozess bei jedem Beendigungspfad (normal,
   Renderer-Crash, hartes Beenden) selbst einholen — dafür gibt es keinen
   Automatismus, und Windows unterscheidet sich hier grundlegend von
   macOS/Linux.

Keiner der vier Bausteine ist trivial falsch zu machen, ohne dass es beim
ersten Testlauf auffällt — außer der Prozessführung bei hartem Beenden, die
sich erst im Drei-Plattform-Beweis (Slice 1) zeigt, wenn niemand mehr manuell
jeden Pfad durchspielt.

## Empfehlung mit Begründung

### 1. Ablage und Pfadauflösung

`extraResources` in der `electron-builder`-Konfiguration verwenden, nicht
`extraFiles` und nicht `files` mit `asarUnpack`. Begründung: `extraResources`
landet außerhalb des ASAR-Archivs, was für ein ausführbares Binary ohnehin
Pflicht ist (`child_process.spawn` kann keine Datei innerhalb eines
ASAR-Archivs ausführen), während `extraFiles` ins App-Root statt ins
Ressourcenverzeichnis kopiert und damit die Konvention bricht, dass Laufzeit-
Assets über `process.resourcesPath` auffindbar sind
([electron.build/docs/contents](https://www.electron.build/docs/contents/)).

```yaml
# electron-builder.yml (Ausschnitt)
extraResources:
  - from: "build/sidecar/${os}/"
    to: "sidecar"
    filter: ["**/*"]
```

Zielverzeichnisse je Plattform (aus derselben Quelle):

| Plattform | Ziel von `extraResources` |
|---|---|
| macOS (`.app`) | `Chappe.app/Contents/Resources/` |
| Windows | `resources/` neben der `.exe` |
| Linux (deb, AppImage) | `resources/` im Installationsverzeichnis bzw. im gemounteten AppImage |

Laufzeit-Auflösung im Hauptprozess:

```js
const sidecarName = process.platform === "win32" ? "chappe.exe" : "chappe";
const sidecarPath = app.isPackaged
  ? path.join(process.resourcesPath, "sidecar", sidecarName)
  : path.join(__dirname, "..", "build", "sidecar", process.platform, sidecarName);
```

`app.isPackaged` ist die von Electron dokumentierte Unterscheidung
Entwicklung/Produktion
([electronjs.org/docs/latest/api/app](https://www.electronjs.org/docs/latest/api/app)).
`process.resourcesPath` zeigt in beiden gepackten Fällen (macOS wie
Windows/Linux) auf das jeweilige `Resources`- bzw. `resources`-Verzeichnis —
ein Bericht bestätigt zusätzlich, dass `process.resourcesPath` in einer
ungepackten `node`-Umgebung nichts zurückliefert, weshalb die
`app.isPackaged`-Prüfung nicht optional ist
([GitHub electron-builder #7293](https://github.com/electron-userland/electron-builder/issues/7293)).

Für **AppImage** gilt keine Sonderbehandlung: Das Image mountet sich beim
Start in ein temporäres Verzeichnis, `process.resourcesPath` zeigt dann auf
einen realen (wenn auch flüchtigen) Pfad innerhalb dieses Mounts — der Code
oben braucht dafür keine Fallunterscheidung. Für **deb** liegt das
Installationsverzeichnis typischerweise unter `/opt/<AppName>/resources`.
Beide Fälle sind durch das generische `process.resourcesPath` abgedeckt;
konkret nachgeprüft werden sollte das trotzdem in Slice 1, da die
electron-builder-Dokumentation den AppImage-Sonderfall nicht explizit
benennt.

**Für das Chappe-Binary selbst**: `chappe rpc` (das im Plan vorgesehene neue
Subkommando) ist ein Konsolenprogramm mit `stdin`/`stdout`-Kommunikation. Es
darf **nicht** mit `--windowed`/`--noconsole` gebaut werden — dieser Modus
setzt `sys.stdout`/`sys.stderr` auf `None` und würde jeden `print()`- oder
Logging-Aufruf zum Absturz bringen
([PyInstaller: Common Issues and Pitfalls](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html)).
Auf Windows bedeutet das Konsolen-Binary standardmäßig ein kurz aufblitzendes
Konsolenfenster beim Start; das lässt sich mit
`subprocess.CREATE_NO_WINDOW` beim `spawn()`-Aufruf auf Windows unterdrücken,
ohne auf `--windowed` umzusteigen — dazu unten mehr unter Fallstricke.

### 2. Zombie-/Orphan-Vermeidung

Der Begriff „Zombie-Prozess" trifft die Situation hier nur bedingt: Ein
Zombie im strengen Unix-Sinn ist ein bereits beendeter Kindprozess, dessen
Exit-Status vom Elternprozess nicht abgeholt wurde (`wait()`) — das passiert
bei Node praktisch nie, weil `child_process` intern via `libuv` reapt, sobald
der Elternprozess selbst läuft. Das eigentliche Risiko bei Chappe ist der
**Orphan**: Das `chappe rpc`-Binary läuft weiter, obwohl der Electron-Prozess,
der es gestartet hat, bereits weg ist — etwa weil der Renderer abstürzt, die
App hart beendet wird (Task-Manager/Kill -9), oder auf Windows der
Installer/Uninstaller den Elternprozess beendet, ohne die Kinder mitzunehmen
([GitHub electron-builder #2516](https://github.com/electron-userland/electron-builder/issues/2516)).

Konkrete Maßnahmen, in dieser Reihenfolge:

**a) Sauberes Beenden im Normalfall.** Sidecar in `app.on('before-quit', …)`
beenden, nicht in `window-all-closed` — auf macOS bleibt die App nach dem
Schließen aller Fenster im Dock aktiv, `window-all-closed` feuert dort
regulär gar nicht als Quit-Signal, während `before-quit` bei Cmd+Q oder
explizitem `app.quit()` zuverlässig vor dem Fensterabbau läuft
([electronjs.org/docs/latest/api/app](https://www.electronjs.org/docs/latest/api/app)).
Ein bekanntes Electron-Issue warnt zusätzlich, dass asynchrone Aufräumarbeit
in `will-quit` teils vor Abschluss abgebrochen wird
([GitHub electron #27201](https://github.com/electron/electron/issues/27201)) —
für Chappe heißt das: den Sidecar-Kill synchron/mit `event.preventDefault()`
+ nachträglichem `app.quit()` in `before-quit` behandeln, nicht auf ein
Promise in `will-quit` verlassen.

**b) Zweistufiges Beenden mit Timeout.** Erst ein sanftes Signal senden,
danach — nur falls der Prozess nach einer kurzen Frist noch lebt — hart
beenden. Diese Zwei-Stufen-Strategie (Standard-Timeout ~1000 ms) wird
verbreitet für genau dieses Problem empfohlen. Auf **macOS/Linux**:
`child.kill('SIGTERM')`, danach bei Bedarf `child.kill('SIGKILL')`. Auf
**Windows existieren POSIX-Signale nicht** — `child.kill()` mit einem
Signalnamen ruft dort intern `TerminateProcess()` auf, was einem sofortigen
`SIGKILL` entspricht; ein cooperatives `SIGTERM` gibt es nicht
([SUSE: SIGKILL vs SIGTERM](https://www.suse.com/c/observability-sigkill-vs-sigterm-a-developers-guide-to-process-termination/),
[Node-Issue zu `kill('SIGINT')` unter Windows](https://github.com/nodejs/node-v0.x-archive/issues/8713)).
Für ein kooperatives Beenden unter Windows bleibt praktisch nur das
Anwendungsprotokoll selbst: ein `{"cmd": "shutdown"}`-JSON-Frame über
`stdin`, auf das `chappe rpc` mit sauberem Verbindungsschluss/DB-Close
reagiert, bevor Electron danach ohnehin `TerminateProcess` als Fallback
schickt.

**c) Prozessbaum statt Einzelprozess beenden.** `child.kill()` beendet nur
den unmittelbaren Kindprozess, keine von ihm selbst gestarteten
Enkelprozesse. Für ein PyInstaller-`--onefile`-Binary ist das relevant, weil
der Bootloader beim Start intern einen zweiten Prozess entpackt/startet
(siehe Abschnitt Datendateien) — auf Windows empfiehlt sich deshalb
`taskkill /PID <pid> /T /F` statt `child.kill()`, weil `/T` den gesamten
Prozessbaum erfasst
([GitHub electron-builder #2894](https://github.com/electron-userland/electron-builder/issues/2894)).
Das npm-Paket `tree-kill` kapselt genau das plattformabhängig (auf Windows
`taskkill /T /F`, auf POSIX Signal an die Prozessgruppe) und ist der in
mehreren Electron-Threads wiederkehrende Standardweg.

**d) macOS-Sonderfall: `spawn()` kann `null` als PID liefern.** Ein offenes
Electron-Issue beschreibt, dass `child_process.spawn()` für ein
PyInstaller-Binary auf macOS ein Objekt mit `pid === null` zurückgeben kann,
obwohl der Prozess tatsächlich läuft — mit Verweis auf einen tiefer
liegenden Node/Electron-Bug, nicht auf PyInstaller selbst
([GitHub electron #17074](https://github.com/electron/electron/issues/17074)).
Workarounds werden dort nicht genannt; das ist ein offener Punkt (siehe
unten) und genau der Grund, warum Slice 0 den Kill-Nachweis „auch nach einem
Absturz des Renderers" explizit als Verifikationskriterium führt.

**e) Notarisierung/Signierung des Sidecar-Binaries selbst.** Für Slice 11
relevant, aber die Weichenstellung muss früher passieren: Ein per
`extraResources` eingebettetes Binary wird von electron-builder **nicht**
automatisch mitsigniert. Für Hardened-Runtime-Notarisierung auf macOS müssen
alle Binärdateien im Bundle signiert sein; dafür wird ein `afterSign`-Hook
oder die `mac.sign`/`mac.binaries`-Konfiguration benötigt (in
electron-builder ab v27 unter dem gemeinsamen `mac.sign`-Objekt)
([electron.build/docs/mac](https://www.electron.build/docs/mac/),
[electron.build/docs/features/code-signing/notarization](https://www.electron.build/docs/features/code-signing/notarization/)).
Ein dokumentierter Fallstrick: Notarisierung schlägt bei manchen Setups
gerade dann fehl, sobald ein externes Binary (`externalBin`/
`extraResources`) hinzukommt — das Entfernen des Sidecars lässt die
Notarisierung wieder durchlaufen, was auf fehlende Signierung des Sidecars
selbst hindeutet, nicht auf einen Fehler im Hauptprozess.

### 3. Datendateien im PyInstaller-Bundle (`schema.sql`)

`Path(__file__).with_name("schema.sql")` in `importer.py:16` funktioniert
**unverändert** unter PyInstaller — das ist der dokumentierte,
empfohlene Weg, nicht ein Sonderfall, den man umschreiben müsste. Der
Bootloader setzt `__file__` beim eingefrorenen Lauf auf den korrekten
absoluten Pfad innerhalb des Bundles, für `--onedir` wie für `--onefile`
identisch:

> „The bootloader sets the `__file__` attribute correctly, so this code
> works identically frozen or unfrozen."
> ([PyInstaller: Run-time Information](https://pyinstaller.org/en/stable/runtime-information.html))

Voraussetzung ist, dass `schema.sql` beim Bauen tatsächlich neben
`importer.py` landet. Das steuert `--add-data`:

```bash
# macOS/Linux (Trenner ':')
pyinstaller --onefile --name chappe \
  --add-data "src/chappe/schema.sql:chappe" \
  src/chappe/__main__.py

# Windows (Trenner ';')
pyinstaller --onefile --name chappe ^
  --add-data "src\chappe\schema.sql;chappe" ^
  src\chappe\__main__.py
```

Der Pfadtrenner in `--add-data source:dest` ist plattformabhängig — Doppelpunkt
auf macOS/Linux, Semikolon auf Windows; PyInstaller vereinheitlicht das nicht
selbst (ein entsprechendes Feature-Request-Issue ist offen)
([GitHub pyinstaller #6320](https://github.com/pyinstaller/pyinstaller/issues/6320)).
Für ein plattformübergreifendes Build-Skript (Slice 1, drei GitHub-Actions-
Runner) heißt das konkret: den Trenner über `os.pathsep` im Python-Build-
Skript wählen, nicht hart im Shell-Aufruf kodieren — oder, robuster, eine
`.spec`-Datei mit `datas=[('src/chappe/schema.sql', 'chappe')]` verwenden,
die plattformunabhängig ist und für ein Projekt mit drei CI-Runnern ohnehin
die wartbarere Lösung ist, weil sie nicht dreimal dieselbe Logik im
Workflow-YAML dupliziert.

`sys._MEIPASS` selbst braucht der Chappe-Code nicht direkt anzufassen — die
`__file__`-basierte Lösung ist genau der von PyInstaller empfohlene Ersatz
dafür. `_MEIPASS` zeigt bei `--onedir` auf den `_internal`-Ordner, bei
`--onefile` auf das temporäre Entpack-Verzeichnis, das der Bootloader bei
jedem Start neu anlegt
([PyInstaller: Run-time Information](https://pyinstaller.org/en/stable/runtime-information.html)) —
relevant nur als Hintergrund für den nächsten Punkt: Bei `--onefile`
entpackt der Bootloader bei **jedem Programmstart** neu in ein Temp-
Verzeichnis, was (a) einen messbaren Start-Overhead bedeutet und (b) auf
Windows von Antiviren-Heuristiken besonders häufig als verdächtig eingestuft
wird (siehe Fallstricke).

### 4. Zeilenbasiertes JSON über stdio

Empfehlung: Auf Node-Seite `readline.createInterface({ input: child.stdout })`
verwenden und zeilenweise `JSON.parse()` aufrufen, nicht selbst auf
`data`-Events und manuelles Puffern setzen — das ist der in der
Node-Dokumentation vorgesehene Weg für zeilenbasierte Stream-Verarbeitung.
Auf Python-Seite jede ausgehende Nachricht mit `flush=True` (oder
äquivalent `sys.stdout.write(...); sys.stdout.flush()`) schreiben.

## Fallstricke

- **Python puffert stdout blockweise, sobald es kein TTY ist.** Ein an
  Electron gepipetes `chappe rpc` schreibt standardmäßig nicht zeilengepuffert,
  sondern erst, wenn der interne Puffer voll ist (typischerweise mehrere KB)
  — der Node-seitige `readline`-Listener bekommt dann minutenlang nichts,
  obwohl Python längst „etwas gesagt" hat. Abhilfe: entweder in jedem
  `print()` `flush=True` setzen, oder den Sidecar-Prozess konsequent mit
  `PYTHONUNBUFFERED=1` in der Environment starten (äquivalent zu Pythons
  `-u`-Flag) — für den `spawn()`-Aufruf also
  `env: { ...process.env, PYTHONUNBUFFERED: "1" }`
  ([pythonpool.com: Unbuffered Python Output](https://www.pythonpool.com/python-unbuffered/),
  [lucadrf.dev: Capture Python subprocess output in real-time](https://lucadrf.dev/blog/python-subprocess-buffers/)).
  Das gilt unabhängig vom Betriebssystem, ist aber in der Praxis der
  Fallstrick, der ein „die App hängt beim ersten RPC-Aufruf" auf allen drei
  Plattformen gleichermaßen erzeugt.

- **`--windowed`/`--noconsole` und `stdin`/`stdout` widersprechen sich
  fundamental.** Für `chappe rpc` gibt es keinen Grund, ohne Konsole zu
  bauen — der Prozess kommuniziert über die Standard-Handles, die im
  windowed-Modus `None` sind. Sollte irgendein importiertes Modul dennoch auf
  `sys.stdout`/`sys.stderr` als Objekt statt als Handle zugreifen, hilft der
  von PyInstaller dokumentierte Dummy-Handle-Trick am Programmanfang
  (`sys.stdout = open(os.devnull, "w")` falls `None`) — für Chappe selbst
  aber eher ein Warnsignal, dass versehentlich der falsche Build-Modus
  gewählt wurde
  ([PyInstaller: Common Issues and Pitfalls](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html)).

- **Auf Windows blitzt bei jedem Konsolen-Binary kurz ein Konsolenfenster
  auf**, wenn man es per `child_process.spawn()` startet, ohne das zu
  unterdrücken. Node bietet dafür die `windowsHide: true`-Option bei
  `spawn()` — sollte für jeden `chappe rpc`-Start gesetzt werden, sonst
  zuckt bei jedem App-Start (und potenziell bei jedem RPC-Neustart) kurz ein
  schwarzes Fenster auf, was für eine Messenger-artige App aus dem Plan
  besonders deplatziert wirkt.

- **`SIGTERM` existiert unter Windows nicht wirklich.** `child.kill('SIGTERM')`
  wird dort zu `TerminateProcess()` — sofortiger, nicht kooperativer Abbruch,
  ohne Chance für `chappe rpc`, die SQLite-Verbindung sauber zu schließen.
  Wer plattformübergreifenden Code schreibt, der auf ein sanftes erstes
  Signal setzt, bekommt auf Windows in Wirklichkeit sofort das harte Ende —
  die einzige verlässliche „sanfte" Abschaltung ist dort das eigene
  Anwendungsprotokoll (`{"cmd":"shutdown"}` über `stdin`), nicht das
  OS-Signal.

- **`child.kill()` erfasst keine Enkelprozesse.** Bei `--onefile` startet der
  PyInstaller-Bootloader intern einen zweiten Prozess (Entpacken + eigentlicher
  Lauf); je nach Plattform und PyInstaller-Version kann das dazu führen, dass
  nach `child.kill()` ein Restprozess übrig bleibt. `--onedir` umgeht dieses
  Zwei-Prozess-Muster vollständig, weil dort nichts mehr entpackt werden
  muss — ein zusätzliches Argument für `--onedir` neben dem
  Antivirus-Punkt unten.

- **PyInstaller-`--onefile`-Binaries werden von Windows Defender und anderen
  AV-Produkten überdurchschnittlich oft als Trojaner/PUP fehlklassifiziert** —
  Ursache ist die Kombination aus Selbstentpackung zur Laufzeit und
  fehlender Code-Signierung, nicht tatsächliches Fehlverhalten. Empfohlene
  Gegenmaßnahmen: `--onedir` statt `--onefile` (wirkt für AV-Heuristiken
  deutlich weniger verdächtig, weil nichts zur Laufzeit selbst entpackt
  wird), Code-Signierung sobald verfügbar (verringert Fehlalarme spürbar,
  eliminiert sie nicht vollständig), und im Zweifel Einreichung bei
  Microsoft zur Whitelist-Prüfung
  ([pythonguis.com: Antivirus False Positives with PyInstaller](https://www.pythonguis.com/faq/problems-with-antivirus-software-and-pyinstaller/),
  [GitHub pyinstaller #6754](https://github.com/pyinstaller/pyinstaller/issues/6754)).
  Für Chappe (Slice 1 baut unsigniert, Slice 11 signiert später) heißt das:
  `--onedir` schon in Slice 1 wählen, nicht erst als spätere Optimierung —
  ein Wechsel von `--onefile` auf `--onedir` verschiebt zusätzlich, wo genau
  `schema.sql` relativ zur `.exe`/zum Binary landet, und sollte deshalb
  nicht nachträglich passieren, nachdem die Pfadauflösung im Hauptprozess
  schon auf `--onefile`-Strukturen einprogrammiert wurde.

- **Notarisierung bricht, wenn der Sidecar mitkommt, aber ungesignt bleibt.**
  Für Slice 11 dokumentiert: Wird ein per `extraResources`/`externalBin`
  eingebettetes Binary nicht explizit mitsigniert, kann die Notarisierung
  des gesamten `.app`-Bundles fehlschlagen, obwohl der Hauptprozess korrekt
  signiert ist — ein `afterSign`-Hook, der auch das `chappe`-Binary signiert,
  ist deshalb kein optionaler Polier-Schritt, sondern Voraussetzung dafür,
  dass macOS die App überhaupt öffnet, sobald Hardened Runtime aktiv ist
  ([GitHub tauri-apps/tauri #11992](https://github.com/tauri-apps/tauri/issues/11992) —
  Tauri, aber dasselbe Signierungsmodell wie bei electron-builder, weil
  beide auf Apples Notarisierungsdienst aufsetzen).

- **`spawn()` kann auf macOS eine `null`-PID zurückliefern**, obwohl der
  PyInstaller-Prozess läuft — siehe oben, ungelöst dokumentiertes
  Electron-Verhalten. Wenn Slice 0 den Kill-Nachweis „auch nach einem
  Absturz des Renderers" führt, sollte der Test explizit prüfen, ob
  `child.pid` überhaupt eine Zahl ist, bevor man sich auf `child.kill()`
  verlässt — sonst bleibt der Fehlerfall unbemerkt, weil `kill()` auf einem
  `null`-Handle still verpufft statt zu werfen.

- **Zeichenkodierung**: `chappe rpc` sollte JSON explizit als UTF-8 schreiben
  (`json.dumps(..., ensure_ascii=False)` plus `.encode("utf-8")`, oder
  `sys.stdout.reconfigure(encoding="utf-8")` am Programmanfang), statt sich
  auf die Locale-Standardkodierung zu verlassen. Auf Windows ist die
  Konsolen-Codepage historisch nicht UTF-8 (oft `cp1252`); ohne explizite
  Umkodierung können deutsche Umlaute in Chatnamen/-inhalten beim
  Serialisieren scheitern oder falsch ankommen. Für diesen konkreten Punkt
  wurde in der Recherche keine primäre PyInstaller-/Python-Dokumentation
  gefunden, die genau diese Kombination (frozen Binary + Windows-Konsolen-
  Codepage + JSON-stdout) behandelt — als **offener Punkt** unten vermerkt,
  weil die Aussage aus allgemeinem Python-3-Encoding-Wissen abgeleitet ist,
  nicht aus einer belegten Quelle zu genau diesem Fall.

## Quellen

- [PyInstaller: Run-time Information](https://pyinstaller.org/en/stable/runtime-information.html) — `sys._MEIPASS`, `sys.frozen`, `__file__`-Strategie
- [PyInstaller: Common Issues and Pitfalls](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html) — `stdout`/`stderr` in windowed-Modus
- [PyInstaller: Using PyInstaller (Manual)](https://pyinstaller.org/en/latest/usage.html) — `--add-data`-Syntax
- [GitHub pyinstaller/pyinstaller #6320](https://github.com/pyinstaller/pyinstaller/issues/6320) — Pfadtrenner `--add-data` plattformabhängig
- [electron-builder: Application Contents](https://www.electron.build/docs/contents/) — `extraResources`/`extraFiles`, Zielverzeichnisse je Plattform
- [electron-builder: macOS](https://www.electron.build/docs/mac/) und [Notarization](https://www.electron.build/docs/features/code-signing/notarization/) — Hardened Runtime, `afterSign`, `mac.sign`
- [Electron: `app`-API-Dokumentation](https://www.electronjs.org/docs/latest/api/app) — `app.isPackaged`, Event-Reihenfolge `before-quit`/`will-quit`/`window-all-closed`
- [GitHub electron/electron #27201](https://github.com/electron/electron/issues/27201) — asynchrone Aufräumarbeit in `will-quit` kann abgebrochen werden
- [GitHub electron/electron #17074](https://github.com/electron/electron/issues/17074) — `spawn()` liefert `null`-PID für PyInstaller-Binary auf macOS
- [GitHub electron-userland/electron-builder #2516](https://github.com/electron-userland/electron-builder/issues/2516) — NSIS-Uninstaller beendet Kindprozesse nicht
- [GitHub electron-userland/electron-builder #2894](https://github.com/electron-userland/electron-builder/issues/2894) — `taskkill /T /F` für Prozessbäume unter Windows
- [GitHub electron-userland/electron-builder #7293](https://github.com/electron-userland/electron-builder/issues/7293) — `process.resourcesPath` in ungepackter Umgebung
- [GitHub nodejs/node-v0.x-archive #8713](https://github.com/nodejs/node-v0.x-archive/issues/8713) — `kill('SIGINT')` unter Windows terminiert sofort
- [SUSE Communities: SIGKILL vs. SIGTERM](https://www.suse.com/c/observability-sigkill-vs-sigterm-a-developers-guide-to-process-termination/) — Signalverhalten, Zwei-Stufen-Terminierung
- [pythonpool.com: Unbuffered Python Output](https://www.pythonpool.com/python-unbuffered/) — `-u`, `flush=True`, `PYTHONUNBUFFERED`
- [lucadrf.dev: Capture Python subprocess output in real-time](https://lucadrf.dev/blog/python-subprocess-buffers/) — Blockpufferung bei Pipes vs. Zeilenpufferung bei TTY
- [pythonguis.com: Antivirus False Positives with PyInstaller](https://www.pythonguis.com/faq/problems-with-antivirus-software-and-pyinstaller/) — `--onedir` vs. `--onefile`, Signierung
- [GitHub pyinstaller/pyinstaller #6754](https://github.com/pyinstaller/pyinstaller/issues/6754) — konkreter AV-Fehlalarm-Fall bei `--onefile`
- [GitHub tauri-apps/tauri #11992](https://github.com/tauri-apps/tauri/issues/11992) — Notarisierung scheitert an ungesignter Sidecar-Binary (Signierungsmodell identisch zu electron-builder, da beide über Apples Notarisierungsdienst laufen)

## Offene Punkte

- **`spawn()`-`null`-PID auf macOS** (electron/electron#17074) ist als
  Symptom dokumentiert, aber ohne bestätigten Workaround. Muss in Slice 0
  praktisch verifiziert werden — nicht klar, ob das aktuelle Electron
  (Stand dieser Recherche keine Versionsnummer geprüft) das Problem noch
  zeigt oder ob es zwischenzeitlich behoben wurde.
- **Zeichenkodierung von stdio unter Windows** (Konsolen-Codepage vs. UTF-8
  bei frozen PyInstaller-Binaries mit JSON-Ausgabe) ist aus allgemeinem
  Python-Wissen abgeleitet, nicht durch eine primäre Quelle zu genau dieser
  Kombination belegt. Vor Slice 1 mit einem echten Windows-Testlauf inkl.
  deutscher Umlaute in Chatnamen verifizieren.
- **Konkrete `electron-builder`-Versionsnummer** wurde nicht recherchiert;
  die `mac.sign`-Konsolidierung „ab v27" stammt aus einem Suchtreffer, nicht
  aus der Versions-Changelog-Seite selbst. Vor dem Schreiben der echten
  `electron-builder.yml` in Slice 1 die tatsächlich installierte Version
  gegen die aktuelle Doku prüfen.
- **AppImage-Pfadauflösung** (`process.resourcesPath` innerhalb des
  gemounteten Images) wurde aus allgemeinem Verständnis von AppImages
  abgeleitet, nicht in der electron-builder-Dokumentation explizit
  bestätigt gefunden. Sollte in Slice 1 an einer echten Linux-VM verifiziert
  werden, wie im Plan ohnehin vorgesehen.
- **SearXNG-Instanz (`meinserver`) war zum Rechercheszeitpunkt nicht
  nutzbar** — alle Suchmaschinen-Engines waren gesperrt
  (`brave: too many requests`, `duckduckgo: timeout`, `startpage: CAPTCHA`,
  `wikipedia: too many requests`). Diese Recherche lief stattdessen über
  `WebSearch`/`WebFetch`. Wert für später: prüfen, ob das ein
  vorübergehendes Rate-Limiting war oder ein Konfigurationsproblem der
  Instanz.
