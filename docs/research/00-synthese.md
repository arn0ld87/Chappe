# Synthese: Von der Recherche zur Entscheidung

Bezug: `docs/gui-plan.md`, sechs Recherchedateien in `docs/research/`
(`sidecar.md`, `virtualisierung.md`, `haertung.md`, `build.md`,
`signal-export.md`, `tokens.md`). Dieses Dokument trifft die Entscheidungen,
die die Recherche offengelassen hat, und benennt, wo sie sich widerspricht.
Stand: 2026-08-26.

## 1. Technologiewahl für Slice 0 und Slice 3

### Slice 0 — Durchstich

| Baustein | Entscheidung | Begründung | Quelle |
|---|---|---|---|
| Packaging-Mechanismus | `extraResources`, nicht `extraFiles`/`asarUnpack` | Einzige Option, die ein ausführbares Binary außerhalb des ASAR-Archivs platziert — `spawn()` kann keine Datei im Archiv ausführen | `sidecar.md` §1 |
| Build-Modus PyInstaller | `--onedir`, nicht `--onefile` | Kein Entpack-Overhead bei jedem Start, weniger AV-Fehlalarme, keine Enkelprozess-Problematik beim Beenden, laut PyInstaller-Doku robuster bei macOS-Notarisierung | `sidecar.md` Fallstricke, `build.md` „PyInstaller-Schritt" — beide konvergieren unabhängig |
| `schema.sql`-Einbindung | `.spec`-Datei mit `datas=[('src/chappe/schema.sql', 'chappe')]`, nicht `--add-data` mit hartkodiertem Trenner | Plattformunabhängig, keine dreifache Duplikation der Trenner-Logik im CI-Workflow | `sidecar.md` §3 (siehe Widerspruch 1 unten) |
| Prozessende | Zweistufig: `before-quit` (nicht `window-all-closed`), sanftes Signal, Timeout, dann hart; auf Windows zusätzlich `{"cmd":"shutdown"}`-JSON-Frame über stdin | `window-all-closed` feuert auf macOS nicht beim Quit; Windows kennt kein kooperatives SIGTERM | `sidecar.md` §2 |
| Prozessbaum-Kill | `tree-kill` (npm) statt einfachem `child.kill()` | Erfasst Enkelprozesse; unter Windows `taskkill /T /F` | `sidecar.md` §2c |
| Renderer-Härtung ab Tag 1 | `contextIsolation: true`, `sandbox: true`, `nodeIntegration: false`, explizit gesetzt, plus IPC-Sender-Validierung in jedem `ipcMain.handle` | Für Chappe „nicht verhandelbar" laut Plan; Defaults reichen laut Härtungsrecherche nicht, wenn Electron selbst einen Durchsetzungs-Bug hat (Element-CVE) | `haertung.md` §1–2, 7; `gui-plan.md` „Nicht verhandelbar" |

### Slice 3 — Design-Fundament und Zeit-Prototyp

| Baustein | Entscheidung | Begründung | Quelle |
|---|---|---|---|
| Token-Pipeline | Eigenbau: `tools/build_tokens.py` (reine Stdlib) liest `design/tokens.json`, erzeugt CSS für beide Konsumenten | Zwei CSS-Konsumenten sind zu wenig, um Node/Style-Dictionary als Pflichtwerkzeug einzuführen; verletzt sonst faktisch die Zero-Dependency-Philosophie für jeden, der nur eine Farbe ändern will | `tokens.md` „Empfehlung" |
| Light/Dark-Mechanik | `@media (prefers-color-scheme: dark)` für den Python-HTML-Export beibehalten; `light-dark()` fürs Electron-Frontend erwägen, sobald Baseline erreicht ist (erwartet 2026-11-13) | Statischer HTML-Export muss auch in älteren Browsern lesbar bleiben; Electron-Frontend läuft in kontrollierter, aktueller Chromium-Runtime | `tokens.md` „Zwei Wege" |
| Dark-Mode-Tiefe | Oberflächenaufhellung über `color-mix(in oklch, …)` plus dezentes `inset`-Randlicht, nicht stärkere Schatten | `box-shadow` ist auf dunklem Grund faktisch unsichtbar, nicht nur schwächer; das heutige `--shadow`-Token in `render/html.py` verschenkt genau deshalb Tiefenwirkung | `tokens.md` „Ästhetik" |
| Neumorphismus | Nur für einzelne Bedienelemente (Suchfeld, Toggle), nicht für die Bubbles | Dichter Fließtext auf Neumorphismus-Flächen ist ein wiederkehrend dokumentiertes Kontrast-/Accessibility-Risiko | `tokens.md` „Ästhetik" |
| Virtualisierungsbibliothek | **virtua** (`virtua/vue`, `VList`), Prop `shift` für das Prepend-Muster | Deckt „ältere Nachrichten oben nachladen" direkt ab, ohne Anchor-Logik selbst zu verdrahten; realer Produktionsnachweis (Rocket.Chat migrierte seine Nachrichtenliste darauf) | `virtualisierung.md` „Empfehlung" |
| Ausdrücklich abgelehnt | vue-virtual-scroller | `DynamicScroller` verwirft im `pageMode` bei **jeder** Höhenänderung eines Elements alle gespeicherten Höhen (Issue #130, offen seit 2019) — trifft Chappes Kernrisiko: 548 von 1.050 Anhängen im Testbackup laden verzögert nach | `virtualisierung.md` „Nicht empfohlen" |
| `estimateSize`-Strategie | Abstands-Anteil der Elementhöhe (abhängig von Δt zwischen Nachrichten) exakt aus `sent_at` vorausberechnen, nur den Inhalts-Anteil (Text, Bild) schätzen lassen | Verkleinert den Fehler, den die Bibliothek sonst per Reconciliation ausgleichen muss — eigene Schlussfolgerung, konsistent mit `estimateSize`/`measureElement`-Mechanik | `virtualisierung.md` Fallstricke (als eigene Schlussfolgerung markiert) |

## 2. Widersprüche zwischen den Recherchen

**Widerspruch 1 — `--add-data`-Trenner: `.spec`-Datei vs. hartkodierter Workflow.**
`sidecar.md` empfiehlt ausdrücklich eine `.spec`-Datei mit
`datas=[('src/chappe/schema.sql', 'chappe')]`, weil das plattformunabhängig
ist und „nicht dreimal dieselbe Logik im Workflow-YAML dupliziert". `build.md`
liefert im „Kompletter Workflow"-Entwurf trotzdem zwei separate
`if: runner.os != 'Windows'` / `if: runner.os == 'Windows'`-Schritte mit
hartkodiertem `:` bzw. `;` direkt im `pyinstaller`-Aufruf — genau die
Duplikation, vor der `sidecar.md` warnt.
**Auflösung:** `sidecar.md` folgen. Eine `.spec`-Datei ersetzt beide
`if`-Zweige in `build.md`s Workflow-Entwurf durch einen einzigen
`pyinstaller chappe.spec`-Aufruf auf allen drei Runnern. Der
`build.md`-Entwurf ist ein Ausgangspunkt, kein bereits abgestimmtes Ergebnis.

**Widerspruch 2 — Zielpfad des Frontends: `app/renderer/` vs. `frontend/`.**
`gui-plan.md` legt die Struktur bereits fest: Vue liegt unter
`app/renderer/`. `tokens.md` wurde unabhängig davon geschrieben und schlägt
`frontend/src/styles/tokens.css` als Ziel des Token-Generators vor —
ein Verzeichnis, das im Plan gar nicht existiert. `tokens.md` selbst markiert
das in den „Offenen Punkten" als unbestätigten Vorschlag.
**Auflösung:** Der Generator schreibt nach `app/renderer/src/styles/tokens.css`
statt `frontend/src/styles/tokens.css`. Rein mechanisch, ändert nichts an der
Empfehlung selbst — nur an einer Konstante im Skript.

**Kein Widerspruch, aber bemerkenswert:** `sidecar.md` und `build.md` kommen
unabhängig voneinander zum selben Ergebnis (`--onedir` statt `--onefile`,
aus teils unterschiedlichen Gründen — Prozessführung beim einen,
Notarisierung und Start-Overhead beim anderen). Das ist ein starkes Signal,
kein Konflikt, wird hier nur festgehalten, damit es nicht als Zufall
missverstanden wird.

## 3. Die drei größten verbliebenen Risiken

**1. Sidecar-Prozessführung ist auf zwei Plattformen ungeklärt, genau dort,
wo Slice 0 seinen Erfolg misst.** Auf macOS kann `child_process.spawn()` für
ein PyInstaller-Binary eine `null`-PID zurückliefern, obwohl der Prozess
läuft (offenes Electron-Issue #17074, kein bestätigter Workaround). Auf
Windows existiert kein kooperatives `SIGTERM` — jedes OS-Signal wird zu einem
sofortigen `TerminateProcess()`, sauberes Beenden braucht zwingend das eigene
`{"cmd":"shutdown"}`-Protokoll. Beides bedroht direkt Slice 0s
Verifikationskriterium „der Python-Prozess ist nachweislich beendet — auch
nach einem Absturz des Renderers".
**Früh prüfen:** Slice-0-Testcode explizit `typeof child.pid === "number"`
prüfen, bevor er sich auf `child.kill()` verlässt, und den geplanten
`{"cmd":"shutdown"}`-Frame schon in Slice 0 (nicht erst Slice 1) gegen eine
echte Windows-VM testen — nicht erst beim „Drei-Plattform-Beweis" zum ersten
Mal Windows anfassen.

**2. `macos-latest` baut inzwischen ausschließlich für Apple Silicon, und der
Plan trifft dazu keine Aussage.** Ein auf `macos-latest` gebautes
PyInstaller-Binary läuft nicht auf Intel-Macs; `actions/setup-python` liefert
dort kein universal2-Python. Das ist keine hypothetische Randnotiz, sondern
eine stillschweigende Einschränkung der Zielgruppe, die `gui-plan.md`
(„macOS, Windows und Linux ab Version 1") nicht benennt.
**Früh prüfen:** Vor dem Schreiben der echten CI-Matrix in Slice 1 klären, ob
Intel-Mac-Support gebraucht wird (siehe Offene Frage in Abschnitt 5) — die
Antwort entscheidet, ob ein zweiter `macos-13`-Runner in die Matrix muss,
bevor überhaupt ein Artefakt existiert, das man testen kann.

**3. Die Zeit-Leitidee aus Slice 3 steht auf einer Bibliothekswahl ohne
unabhängige Belege bei Chappes tatsächlicher Größenordnung.** Für virtua wie
für TanStack Virtual existieren keine unabhängigen Benchmarks bei ~39.000
Items mit echten variablen Höhen — alle Zahlen stammen aus den Blogs der
Projektbetreiber selbst. Dazu kommt ein dokumentiertes `scrollToIndex`-Problem
bei ~10.000 Items in TanStack (Issue #216), das die Genauigkeit von
Zeitstrahl-Sprüngen bei Chappes Datenmenge in Frage stellt. Slice 3 selbst
benennt das Risiko bereits als hoch — die Recherche bestätigt, dass es nicht
theoretisch ist.
**Früh prüfen:** Den virtua-Prototyp so früh wie möglich in Slice 3 gegen das
**echte** 39.274-Nachrichten-Backup bauen, nicht gegen die synthetische
Testfixture — und den Zeitstrahl-Sprung explizit mit einer
Reconciliation-Prüfung nach dem Render testen, nicht mit einem einzelnen
`scrollToIndex`-Aufruf.

## 4. Was sich am Plan ändern müsste

Nichts Grundsätzliches — keine Recherche widerlegt eine der
„Grundentscheidungen" oder „Nicht verhandelbar"-Punkte in `gui-plan.md`. Zwei
Ergänzungen sind trotzdem angezeigt, weil die Recherche eine Lücke im
bestehenden Zuschnitt zeigt, keinen Fehler:

- **Slice 2 (RPC-Vertrag) fehlt eine Steuernachricht.** Der Plan listet nur
  fachliche Methoden (Backups, Chats, Verlauf, Suche, …). Für sauberes
  Beenden auf Windows braucht das Protokoll zusätzlich einen
  `{"cmd":"shutdown"}`-Rahmen, den `chappe rpc` mit Verbindungsschluss/DB-Close
  beantwortet, bevor Electron mit `TerminateProcess()` nachfasst
  (`sidecar.md` §2b). Sollte als Teil des „einheitlichen Fehlerformats" in
  Slice 2 mitgedacht werden, nicht als nachträglicher Patch.
- **Slice 1s Risikobeschreibung benennt den falschen Schwerpunkt.** Der Plan
  schreibt: „PyInstaller verhält sich je Plattform unterschiedlich, besonders
  beim Auffinden von `schema.sql`". Die Recherche zeigt: Die
  `__file__`-basierte Pfadauflösung funktioniert laut PyInstaller-Doku
  unverändert und ist kein Sonderfall (`sidecar.md` §3) — vorausgesetzt,
  `--add-data`/`.spec` ist korrekt. Das tatsächlich größere, im Plan nicht
  erwähnte Risiko ist die arm64-only-Architektur von `macos-latest` (Risiko 2
  oben). Die Formulierung in `gui-plan.md` sollte diesen Schwerpunkt
  verschieben, wenn der Plan das nächste Mal überarbeitet wird.

Eine dritte Beobachtung, kein Recherche-Widerspruch, aber ein Fakt, der beim
nächsten Überarbeiten von `gui-plan.md` korrigiert gehört: Der Plan spricht
zweimal von „49 Tests" (Grundentscheidungen-Tabelle, Slice 2). Die Testsuite
hat aktuell **56 Tests** (`PYTHONPATH=src python3 -m pytest tests -q`,
lokal verifiziert). Ob die 49 aus einem älteren Stand stammen oder ein
Zähl-/Tippfehler sind, ließ sich aus dem Repo-Verlauf nicht klären — vor
Slice 2 kurz gegenprüfen, damit die Verifikationszahl im Plan stimmt.

## 5. Offene Fragen für Alex

1. **Intel-Mac-Support für Version 1: ja oder nein?** Wenn ja, braucht Slice 1
   einen zweiten `macos-13`-Runner und zwei getrennte `mac`-Artefakte
   (x64/arm64) statt eines universellen Builds — reine
   Produktentscheidung, abhängig von der erwarteten Nutzerbasis, nicht
   recherchierbar (`build.md` „Offene Punkte").
2. **Automatische Updates — jetzt entscheiden oder wirklich erst bei Slice
   11?** Der Plan markiert das selbst als offen, verschiebt die Entscheidung
   aber. Die Recherche ändert daran nichts Neues, bestätigt nur die
   Konsequenz: Sobald Auto-Update kommt, wird Signierung zur harten
   Voraussetzung statt zum späteren Meilenstein — das zieht ggf. Slice 11
   nach vorne.
3. **Zeitstrahl-Scrubber-UI hat keine Referenzimplementierung in keiner der
   drei Bibliotheken.** Das ist keine technische Frage mehr, sondern eine
   Designfrage: Wie soll ein Scrubber bei stark ungleich verteilten
   Zeitabständen (z. B. Wochen ganz ohne Nachrichten) aussehen und sich
   verhalten? Braucht eine Entscheidung, bevor Slice 3 den Prototyp baut,
   nicht erst danach.
4. **DTCG-Konformität: eigene angelehnte Konvention dauerhaft, oder Migration
   zu einem strikten DTCG-2025.10-Dokument vorsehen?** Die empfohlene Lösung
   ist bewusst kein vollständiges DTCG-Dokument (kein `$schema`, eigene
   `light`/`dark`-Konvention statt Resolver-Modulen). Das ist für den
   aktuellen Zweck ausreichend, macht aber einen späteren Wechsel zu einem
   Standard-Tool (Style Dictionary, Terrazzo) aufwändiger, falls das je nötig
   wird — abzuwägen gegen den Umstand, dass das Tool-Ökosystem selbst noch
   nachzieht (`tokens.md` „Fallstricke").
