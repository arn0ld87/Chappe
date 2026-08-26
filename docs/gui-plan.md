# Umsetzungsplan: Chappe als Desktop-App

Ergebnis der Grilling-Sitzung vom 2026-08-26. Dieses Dokument hält die
getroffenen Entscheidungen fest und schneidet die Arbeit in Slices. Es enthält
keinen Code und keine Implementierungsdetails, die sich beim Bauen ohnehin
verschieben — nur Ziel, Umfang, Verifikation und Risiko je Slice.

## Grundentscheidungen

Die GUI ist eine **zusätzliche Schicht**, kein Ersatz. `cli.py` bleibt
unangetastet, weil `chappe sql` und Scripting in keiner Klick-Oberfläche eine
gute Heimat finden — und weil die 49 Tests daran hängen.

| Achse | Entscheidung |
|---|---|
| Shell | Electron; macOS, Windows und Linux ab Version 1 |
| Backend | `chappe` als PyInstaller-Binary, Kindprozess von Electron, zeilenweise JSON über stdin/stdout |
| Frontend | Vue 3 + TypeScript + Vite |
| Grundform | Messenger-Nachbau: Chatliste links, Verlauf in Bubbles rechts, Suche oben |
| Leitidee | **Zeit wird spürbar** — Pausen als sichtbarer Abstand, Jahreswechsel als Zäsur, Zeitstrahl am Rand |
| Optik | Warm-taktil, weiche Materialität; hell und dunkel, folgt dem System |
| Onboarding | Auto-Suche nach `main.jsonl`, sonst bebilderte Anleitung durch den Signal-Desktop-Export |
| Mehrere Konten | Kontowechsler, immer genau ein Backup sichtbar; keine Zusammenführung |
| Umfang | Alle CLI-Funktionen inklusive SQL-Konsole, **außer `--keep-secrets`** |
| Medien | Beim Import per Hardlink (sonst Kopie) ins App-Verzeichnis |
| HTML-Export | Bleibt eigenständig; geteilt werden nur die Design-Tokens |
| Sprache | Deutsch, Texte zentral in einer Sprachdatei |
| Auslieferung | Erst unsigniert, Pipeline von Anfang an signierfähig |

### Was sich dadurch **nicht** ändert

Das Python-Paket `chappe` behält **null Laufzeit-Abhängigkeiten**. PyInstaller
ist Build-, keine Laufzeit-Abhängigkeit. Die Zero-Dependency-Regel aus
`CLAUDE.md` gilt weiterhin für `src/chappe/` und wird lediglich präzisiert:
sie gilt für das Paket, nicht für die neue App-Schicht in `app/`.

Ebenfalls unverändert: alle Invarianten aus `CLAUDE.md`. Medienbindung
ausschließlich über `plaintext_hash`, Chatfilterung über `chat_id` statt
Namensteile, `revision_of IS NULL` in jeder Auswertung, Secret-Filter beim
Import.

### Nicht verhandelbar

- **Der RPC-Adapter ruft `query.py` direkt auf.** Er parst niemals die
  Textausgabe der CLI. Wer das später anders macht, koppelt die App an
  Formatierungsentscheidungen und bricht sie mit jedem Feinschliff.
- **Electron-Härtung**: `contextIsolation` an, `nodeIntegration` aus, Sandbox
  an, strikte CSP, keine externen Ressourcen. Diese App sieht `svrPin`,
  `profileKey` und die `identityKey` aller Kontakte.
- **`--keep-secrets` erreicht die GUI nicht** — auch nicht versteckt, auch
  nicht hinter einer Warnung.

## Struktur

```
chappe/
├─ src/chappe/          Python-Paket, dependency-frei
│  └─ rpc.py            NEU: Adapter über query/importer/media
├─ app/                 NEU: Electron + Vue
│  ├─ main/             Hauptprozess, Sidecar-Verwaltung, Protokoll-Handler
│  ├─ preload/          Brücke, minimal und explizit
│  └─ renderer/         Vue 3
├─ design/tokens.json   NEU: eine Quelle für Vue-CSS und render/html.py
└─ docs/gui-plan.md     dieses Dokument
```

Ablage von Datenbank und Medien im plattformüblichen App-Verzeichnis:
`~/Library/Application Support/Chappe`, `%APPDATA%\Chappe`,
`~/.local/share/chappe`.

---

## Slices

Die Reihenfolge folgt dem Risiko, nicht der Sichtbarkeit. Die beiden größten
Unbekannten — trägt die Sidecar-Kette auf drei Plattformen, und lässt sich die
Zeit-Idee über 39.000 Nachrichten flüssig darstellen — werden zuerst
beantwortet, solange ein Umsteuern noch billig ist.

### Slice 0 — Durchstich

**Ziel:** Beweisen, dass Electron ein PyInstaller-Binary starten, befragen und
sauber beenden kann.

Electron-Grundgerüst, `chappe rpc` als neues Subkommando mit genau einer
Methode (`list_chats`), PyInstaller-Spec, Sidecar-Start im Hauptprozess,
ungestylte Liste im Renderer. Bewusst hässlich.

**Verifikation:** Die App startet lokal auf macOS, zeigt die echten Chats aus
einer vorhandenen Datenbank, und der Python-Prozess ist nach dem Schließen
nachweislich beendet — auch nach einem Absturz des Renderers.

**Risiko:** hoch. Hier entscheidet sich, ob die Architektur trägt.

### Slice 1 — Drei-Plattform-Beweis

**Ziel:** Slice 0 läuft auf macOS, Windows und Linux.

GitHub Actions mit drei Runnern, PyInstaller je Plattform, Electron-Builder
erzeugt herunterladbare Artefakte. Noch keine Signierung, aber die Pipeline
wird so gebaut, dass Signierung später nur Secrets und zwei Schritte sind.

**Verifikation:** Auf jeder der drei Plattformen startet das Artefakt und zeigt
Chats. Windows und Linux über VMs.

**Risiko:** hoch. PyInstaller verhält sich je Plattform unterschiedlich,
besonders beim Auffinden von `schema.sql`.

### Slice 2 — RPC-Vertrag

**Ziel:** Die vollständige Schnittstelle zwischen Python und App steht und ist
geprüft.

Alle Methoden, die die GUI je brauchen wird: Backups, Chats, Verlauf mit
Seitenweise-Abruf, Suche, Statistik, Medien, Import mit Fortschritts-Ereignissen,
Export, SQL. Dazu ein einheitliches Fehlerformat und ein Ereignisstrom für
lange Vorgänge.

**Verifikation:** pytest-Tests für jede Methode gegen das synthetische
Mini-Backup aus `tests/fixtures/`. Die bestehenden 49 Tests laufen unverändert
weiter.

**Risiko:** mittel. Der Ort, an dem sich später Fehler am teuersten rächen.

### Slice 3 — Design-Fundament und Zeit-Prototyp

**Ziel:** Die Designsprache steht, und die Leitidee ist an echten Daten
bewiesen.

Token-Datei als einzige Quelle für Farben, Abstände, Schriften und Materialität
in hell und dunkel; Anbindung an Vue-CSS und an `render/html.py`. Parallel: der
virtualisierte Verlauf mit variablen Elementhöhen — Pausen als Abstand,
Jahreszäsuren, Zeitstrahl am Rand — an einem echten Chat mit 39.000 Nachrichten.

**Verifikation:** Flüssiges Scrollen und Springen im großen Chat, keine
springenden Positionen beim Nachladen, beide Themes durchgehend korrekt.

**Risiko:** hoch. Variable Höhen und Virtualisierung vertragen sich schlecht;
wenn die Leitidee hier nicht trägt, muss sie angepasst werden, bevor die
gesamte Oberfläche darauf aufbaut.

### Slice 4 — Vertikale Scheibe in Endqualität

**Ziel:** Ein durchgehender Pfad, den man jemandem zeigen kann.

Onboarding mit Suche nach `main.jsonl` in Downloads, Schreibtisch und
Dokumenten, bebilderter Anleitung als Rückfall und Ordnerauswahl; Import mit
nicht-blockierendem Fortschritt, Restzeit und Abbruch; Medien-Übernahme per
Hardlink mit ehrlicher Platzanzeige und dem Hinweis, dass der Export-Ordner nun
gelöscht werden darf; Chatliste; Verlauf lesen. Alles im endgültigen Design.

**Verifikation:** Eine Person ohne technischen Hintergrund kommt vom ersten
Start bis zum gelesenen Chat, ohne dass jemand daneben sitzt. Die vier Minuten
Import beim großen Backup wirken erklärt, nicht kaputt.

**Risiko:** mittel. Die TCC-Berechtigungsdialoge auf macOS müssen erklärt
werden, bevor sie erscheinen, sonst wirken sie bedrohlich.

### Slice 5 — Suche

Volltextsuche mit Trefferliste, Umgebungsanzeige und Sprung in den Verlauf;
wörtliche Suche als Umschalter; Einschränkung auf Chat und Zeitraum.

**Verifikation:** Ein Treffer aus dem Jahr 2017 lässt sich anklicken und der
Verlauf steht an der richtigen Stelle.

### Slice 6 — Medien

Protokoll-Handler für `chappe://media/…`, Bilder und Videos im Verlauf,
abspielbare Sprachnachrichten mit Wellenform, Medien-Galerie je Chat, Export in
einen Ordner unter sprechenden Namen.

**Verifikation:** Anhänge ohne lokale Datei (`local_path IS NULL`, im kleinen
Backup 548 von 1.050) werden als „nicht heruntergeladen" kenntlich gemacht,
nicht als Fehler.

### Slice 7 — Auswertungen

Nachrichten pro Person, Monat, Wochentag und Stunde; häufigste Wörter und
Reaktionen; Anrufbilanz; Median-Antwortzeiten. Im warm-taktilen Design, nicht
als Diagrammbibliothek-Standardausgabe.

### Slice 8 — Export

Chat als Webseite sichern (ruft den bestehenden HTML-Export auf), Anhänge in
einen Ordner legen, dazu die übrigen Formate aus `cmd_export`.

### Slice 9 — Erweitert

SQL-Konsole mit Ergebnisdarstellung, Quellenverwaltung, Kontowechsler,
Einstellungen, Theme-Umschalter. Der SQL-Bereich bleibt lesend
(`PRAGMA query_only`), wie in der CLI.

### Slice 10 — Härtung und Politur

Leerzustände, verständliche Fehlermeldungen ohne Stacktraces, vollständige
Tastaturbedienung, Kontrastprüfung in beiden Themes, Verhalten bei fehlender
oder beschädigter Datenbank. Playwright-Electron für drei Pfade: Onboarding bis
zum ersten Chat, Import mit Fortschritt, Suche mit Treffer.

### Slice 11 — Auslieferung

Developer ID und Notarisierung für macOS, Codesigning für Windows,
Installer-Formate je Plattform.

**Offen:** Automatische Updates. Ohne sie holt niemand aus der Zielgruppe je
eine neue Version. Mit ihnen wird Signierung zur harten Voraussetzung statt zum
späteren Meilenstein — die Entscheidung verschiebt Slice 11 nach vorne und ist
noch nicht getroffen.

---

## Was nicht in diesem Plan steckt

- **Die Screenshots für die Anleitung.** Sie müssen aus einer echten
  Signal-Desktop-Installation stammen, ohne private Daten im Bild. Handarbeit.
- **Zusammenführung beider Konten.** Bewusst zurückgestellt; der Kontowechsler
  löst das Problem für Version 1 vollständig. Eine spätere Merge-Ansicht setzt
  über `v_messages` auf, nicht hinein — siehe `CLAUDE.md`.
- **`CLAUDE.md` selbst.** Die Zero-Dependency-Regel wird präzisiert, sobald
  Slice 0 steht und die Struktur real ist, nicht vorher.
