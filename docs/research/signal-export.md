# Signal-Desktop-Backup-Export: aktueller Ablauf aus Nutzersicht (2026)

Recherchestand: 2026-08-26. Primärquelle ist der öffentliche Quellcode von
`signalapp/Signal-Desktop` (GitHub), abgeglichen mit dem aktuellen Stable-Tag
**v8.24.1** (veröffentlicht 2026-08-20) und dem `main`-Branch
(`8.27.0-alpha.1`). Ergänzt durch reale Nutzerberichte aus GitHub Issues und
die offiziellen deutschen Signal-Support-Artikel, soweit vorhanden.

## Abriss

Signal Desktop hat **zwei unterschiedliche, leicht verwechselbare
Export-Funktionen**. Nur eine davon erzeugt das Format, das Chappe liest
(`main.jsonl` + `files/`):

| | **„Export chat history"** (Chappe-relevant) | „Lokale Backups" (Desktop-Backups) |
|---|---|---|
| Menüpfad | Einstellungen → **Chats** → Abschnitt „Chat-Verlauf exportieren" | Einstellungen → **Backups** → „Weitere Backup-Möglichkeiten" → Einrichten |
| Zweck | Menschen-/maschinenlesbarer Export zur Auswertung/Archivierung | Verschlüsseltes Backup zur Wiederherstellung auf einem **Android**-Hauptgerät |
| Ausgabeformat | Klartext-JSON-Lines: `signal-export-<Zeitstempel>/main.jsonl` + `metadata.json` + optional `files/` | Verschlüsselt (Protobuf/AES-256-CTR): `SignalBackups/signal-backup-<Zeitstempel>/main` + `files/` |
| Voraussetzung | OS-Authentifizierung (Touch ID / Windows Hello / Linux-Polkit) | 64-stelliger Wiederherstellungsschlüssel |
| Interne Bezeichnung im Quellcode | `PlaintextExportWorkflow` / `exportPlaintext` | `LocalBackupExportWorkflow` / `exportLocalBackup` |
| iOS wiederherstellbar? | irrelevant (kein Wiederherstellungsziel) | Nein |

**Dieses Dokument beschreibt ausschließlich die erste Spalte** — „Export chat
history", intern `PlaintextExportWorkflow` genannt —, weil nur sie
`main.jsonl` und `files/` erzeugt (Quelle: `ts/services/backups/index.preload.ts`,
Zeilen 660–695, Tag `v8.24.1`). Die zweite Spalte ist eine komplett andere
Funktion mit anderem Zweck, anderem Speicherformat und anderer
Schlüsselverwaltung; sie taucht in Suchergebnissen und Support-Artikeln
prominent auf und wird leicht verwechselt (siehe Fallstricke).

## Empfehlung mit Begründung

Für das bebilderte Onboarding folgenden Ablauf zeigen, jeder Schritt ist
gegen den aktuellen Quellcode verifiziert:

1. **Einstellungen öffnen** (Zahnrad/Drei-Punkte-Menü bzw. `Cmd/Strg+,`) →
   Tab **„Chats"** (`icu:Preferences__button--chats` = „Chats").
2. Im unteren Bereich der Chats-Seite, unterhalb der Chat-Ordner-Sektion:
   Abschnitt **„Chat-Verlauf exportieren"** mit Beschreibungstext
   „Exportiere eine maschinenlesbare JSON-Kopie all deiner Chats.
   Verschwindende Nachrichten werden nicht exportiert." und Button
   **„Exportieren"**.
   Quelle: `ts/components/Preferences.dom.tsx`, Zeilen ~1327–1345 (Tag `v8.24.1`);
   i18n-Keys `icu:PlaintextExport--PreferencesRow--*`.
3. Klick auf „Exportieren" öffnet einen Dialog **„Chat-Verlauf
   exportieren?"** mit Warntext („VORSICHT! Gib diese Datei an niemanden
   weiter…") und einer Checkbox **„Medien einbeziehen (größere Datei)"**,
   standardmäßig **aktiviert**. Button „Fortfahren".
   (Schritt intern: `ConfirmingExport`.)
4. **OS-Authentifizierung** (`ConfirmingWithOS`): Signal ruft eine
   plattformeigene Bestätigung auf, bevor der Export beginnt —
   kein Signal-PIN, kein Wiederherstellungsschlüssel:
   - **macOS**: Touch-ID-Prompt (`systemPreferences.promptTouchID`), Text
     „…deinen Chat-Verlauf exportieren".
   - **Windows**: Windows Hello über die `UserConsentVerifier`-API, Text
     „Bestätige deine Identität, um deinen Chat-Verlauf zu exportieren."
   - **Linux**: `pkcheck` gegen die Polkit-Action-ID
     `org.signalapp.plaintext-export` (Policy-Datei
     `build/policy-templates/org.signalapp.plaintext-export.policy` wird mit
     dem Paket installiert).
   Quelle: `ts/util/os/promptOSAuthMain.main.ts` (Tag `v8.24.1`).
5. **Zielordner wählen** (`ChoosingLocation`): nativer Betriebssystem-Dialog
   „Ordner auswählen" (Electron `dialog.showOpenDialog`, Eigenschaft
   `openDirectory, createDirectory` — ein neuer Ordner kann direkt im Dialog
   angelegt werden). **Es gibt keinen fest voreingestellten Zielordner**; der
   Dialog öffnet dort, wo das Betriebssystem/Electron zuletzt stand (i. d. R.
   zuletzt genutzter Ordner oder Home-Verzeichnis) — Chappes Doku-Grundannahme
   „exportierte Backup-Verzeichnisse" muss also mit „irgendwo vom Nutzer
   gewählt" beschrieben werden, nicht mit einem Standardpfad.
   Quelle: `ts/state/ducks/backups.preload.ts` Funktion
   `showExportLocationChooser`; `app/main.main.ts` Handler
   `show-open-folder-dialog` (beide Tag `v8.24.1`).
6. **Export läuft** (`ExportingMessages` → `ExportingAttachments`):
   Fortschrittsanzeige „Chat-Verlauf wird exportiert" mit Byte-Fortschritt
   und Hinweis „Dies kann einige Minuten dauern".
7. **Abschluss** (`Complete`): Dialog „Export abgeschlossen" mit erneuter
   Datenschutzwarnung und Button „Im Finder anzeigen" (macOS) bzw. „Im
   Ordner anzeigen" (Windows/Linux), der den erzeugten Ordner öffnet.

**Ergebnis auf der Platte** — exakt der Ordner, den Chappe erwartet:

```
<vom Nutzer gewählter Ordner>/
└── signal-export-YYYY-MM-DD-HH-mm-ss/
    ├── main.jsonl
    ├── metadata.json          # {"version": 1}
    └── files/                 # nur wenn „Medien einbeziehen" angehakt war
        ├── a1/
        │   └── a1bcdef...     # sharded nach den ersten 2 Zeichen des mediaName
        └── b2/
            └── b2fa9c...
```

Quelle: `join(targetPath, 'signal-export-${getTimestampForFolder()}')` sowie
`getLocalBackupDirectoryForMediaName`/`getLocalBackupPathForMediaName`
(`ts/services/backups/index.preload.ts` und
`ts/services/backups/util/localBackup.node.ts`, Tag `v8.24.1`). Das
Ordnerpräfix ist `signal-export-`, nicht `signal-backup-` (letzteres ist das
verschlüsselte Format der anderen Funktion).

**Begründung für diese Struktur im Onboarding**: Der bebilderte Screenshot-
Pfad sollte exakt diese sieben Schritte in dieser Reihenfolge zeigen, weil
jeder Schritt einen eigenen, vom Nutzer bestätigten Dialog erzeugt (kein
automatischer Ablauf im Hintergrund) — Nutzer, die einen Schritt
überspringen oder verwechseln (v. a. Schritt 4/5), landen im Fehlerdialog
oder im falschen Backup-Typ. Die Tabelle oben sollte im Onboarding als
Warnkasten vorangestellt werden, damit Nutzer nicht bei „Einstellungen →
Backups" landen und dort vergeblich einen 64-stelligen Schlüssel suchen.

### Unterschiede zwischen macOS, Windows, Linux

Der Ablauf selbst (Schritte 1–7) ist auf allen drei Plattformen identisch;
die Unterschiede liegen ausschließlich in:

- **Wortlaut/Mechanik der OS-Authentifizierung** (Touch ID / Windows Hello /
  Polkit, siehe oben).
- **Label des „Im Ordner anzeigen"-Buttons**: „Im Finder anzeigen" (macOS)
  vs. „Im Ordner anzeigen" (Windows, Linux) — `icu:PlaintextExport--CompleteDialog--ShowFiles--{Mac,Windows,Linux}`.
- **Verfügbarkeit der OS-Authentifizierung selbst**: Fehlt auf einem Gerät
  eine geeignete Hardware/Konfiguration, wird der Schritt übersprungen statt
  den Export zu blockieren — mit einer wichtigen Ausnahme auf macOS (siehe
  Fallstricke).

Die App-eigenen Datenverzeichnisse (nicht der Exportzielordner, sondern wo
Signal Desktop selbst seine laufende, verschlüsselte Datenbank hält) liegen
plattformabhängig unter:

- macOS: `~/Library/Application Support/Signal`
- Windows: `%APPDATA%\Signal` (`C:\Users\<Name>\AppData\Roaming\Signal`)
- Linux: `~/.config/Signal`

Diese drei Pfade sind **nicht** der Ort, an dem `signal-export-*` landet —
das ist immer der vom Nutzer im Dialog gewählte Ordner. Sie sind nur relevant,
falls das Onboarding zusätzlich erklärt, wo Signal Desktop selbst seine Daten
hält (z. B. für Deinstallations-/Backup-Hinweise).

## Fallstricke

- **Verwechslung der beiden Export-Funktionen.** Die meisten Web-Treffer
  (auch der offizielle deutsche Support-Artikel „Lokale Signal
  Desktop-Backups") behandeln die verschlüsselte Android-Backup-Funktion,
  nicht die hier relevante. Ein Nutzer, der nach „Signal Desktop Backup"
  sucht, landet mit hoher Wahrscheinlichkeit zuerst bei „Einstellungen →
  Backups" statt „Einstellungen → Chats". GitHub-Issue #7873 zeigt genau
  diese Verwechslung in einem reinen Support-Ticket.
- **Verschwindende Nachrichten fehlen im Export**, ausdrücklich so
  dokumentiert im UI-Text („Verschwindende Nachrichten werden nicht
  exportiert") und im Quellcode (`ts/services/backups/export.preload.ts`:
  „All disappearing messages are excluded in plaintext export"). Für ein
  Onboarding, das Vollständigkeit verspricht, ein wichtiger Disclaimer.
- **Kein Standard-Zielordner.** Der native Ordnerauswahl-Dialog hat keinen
  von Signal vorgegebenen Startpunkt. Screenshots, die einen bestimmten
  Pfad („z. B. Desktop") als „Standard" darstellen, wären erfunden — das ist
  in der Recherche nicht belegbar.
- **Der Ordnerauswahl-Dialog trägt einen fachfremden Titel.** Er wird über
  denselben internen Dialog wie der Anhang-Speicherdialog aufgerufen und
  zeigt deshalb den Titel **„Anhänge speichern"** (`icu:SaveMultiDialog__title`)
  statt eines exportspezifischen Titels wie „Speicherort wählen". Das ist
  keine Fehlfunktion, aber ein Screenshot-Fallstrick: Nutzer könnten den
  Dialog für falsch/verwirrend halten. Quelle: `ts/state/ducks/backups.preload.ts`,
  Funktion `showExportLocationChooser` (Tag `v8.24.1`).
- **macOS ohne Touch-ID-Hardware — Verhalten unklar/riskant.** Der Code ruft
  `systemPreferences.promptTouchID()` ohne vorherige Prüfung, ob überhaupt
  Touch-ID-Hardware vorhanden ist; jeder Fehler (auch „nicht verfügbar")
  wird als `unauthorized` behandelt und **bricht den Export ab**
  (`clearWorkflow`), anders als bei Windows/Linux, wo eine fehlende
  Voraussetzung explizit als `unsupported` erkannt und der Export trotzdem
  fortgesetzt wird. Das ist aus dem Quellcode abgeleitet, nicht durch einen
  Support-Artikel oder Bug-Report bestätigt — siehe „Offene Punkte".
- **Frühe Versionen waren fehleranfällig.** GitHub-Issue #7731 („Export Chat
  History fails", 9 Kommentare) dokumentiert wiederholte Abbrüche mit
  „Couldn't export chat history" auf Windows 10/11 zwischen den
  Signal-Versionen 7.90.0 und mindestens 8.11.0 (Februar/März 2026), von
  Signal-Mitarbeitern als Bug bestätigt und in einem Folge-Release behoben.
  Bezieht sich ein Onboarding auf ältere Signal-Versionen (< 8.12 etwa),
  sollte ein Hinweis „bei Fehlern zunächst auf die neueste Version
  aktualisieren" stehen.
- **Mindest-Speicherplatz-Prüfungen können den Export abbrechen.** Vor
  Beginn wird geprüft, ob mindestens 200 MiB frei sind
  (`MIMINUM_DISK_SPACE_FOR_LOCAL_EXPORT`); vor dem Anhänge-Download erneut,
  diesmal gegen die tatsächlich benötigte Anhangsgröße plus 100 MiB Puffer.
  Bei zu wenig Platz erscheint ein spezifischer Fehlerdialog mit der
  benötigten Zusatzgröße in Bytes.
- **`files/` ist zweistufig sharded, nicht flach.** Dateien liegen unter
  `files/<erste 2 Zeichen von mediaName>/<mediaName>[.<Erweiterung>]`, nicht
  direkt unter `files/`. Das bricht Chappes hash-basierte Zuordnung nicht
  (Invariante 1 in `CLAUDE.md` hasht ohnehin jede Datei im Backup-Verzeichnis
  rekursiv), ist aber für Screenshots der Ordnerstruktur im Onboarding
  relevant — ein Screenshot mit flachem `files/`-Inhalt wäre falsch.
- **Möglicherweise neue Dateinamens-Erweiterung.** Der aktuelle Quellcode
  hängt bei `isPlaintextExport` eine aus dem Content-Type abgeleitete
  Dateiendung an den Dateinamen (`ts/jobs/AttachmentLocalBackupManager.preload.ts`,
  Funktion `runAttachmentBackupJob`). Ob das bereits so war, als die beiden
  lokal vorliegenden echten Backups (siehe Haupt-`CLAUDE.md`) erzeugt
  wurden, ließ sich in dieser Recherche nicht klären — vor dem Erstellen von
  Screenshots mit sichtbaren Dateinamen sollte das gegen einen frischen
  Export desselben Chappe-Nutzers geprüft werden.
- **Kein offizieller Support-Artikel für genau diese Funktion gefunden.**
  Weder die deutsche noch die englische Signal-Support-Seite
  (`support.signal.org`) hat (Stand der Recherche) einen Artikel, der
  „Export chat history" / „Chat-Verlauf exportieren" beschreibt — nur die
  In-App-Texte und der Quellcode belegen den Ablauf. Für das Onboarding
  heißt das: Es gibt keine offizielle Zweitquelle zum Gegenprüfen von
  Screenshots, nur den quelloffenen Code und reale Nutzerberichte.

## Quellen

- Quellcode `signalapp/Signal-Desktop`, Tag `v8.24.1` (aktueller Stable,
  veröffentlicht 2026-08-20) und `main` (`8.27.0-alpha.1`), abgerufen via
  GitHub-API/`gh`:
  - `ts/components/PlaintextExportWorkflow.dom.tsx` — UI-Zustandsautomat der
    Dialoge (Schritte `ConfirmingExport` → `ConfirmingWithOS` →
    `ChoosingLocation` → `ExportingMessages`/`ExportingAttachments` →
    `Complete`/`Error`).
    <https://github.com/signalapp/Signal-Desktop/blob/v8.24.1/ts/components/PlaintextExportWorkflow.dom.tsx>
  - `ts/types/LocalExport.std.ts` — Typdefinitionen beider Export-Varianten
    (`PlaintextExportSteps` vs. `LocalBackupExportSteps`).
    <https://github.com/signalapp/Signal-Desktop/blob/v8.24.1/ts/types/LocalExport.std.ts>
  - `ts/components/Preferences.dom.tsx` (Zeilen ~1327–1345) — Menüpfad
    Einstellungen → Chats → „Chat-Verlauf exportieren".
    <https://github.com/signalapp/Signal-Desktop/blob/v8.24.1/ts/components/Preferences.dom.tsx>
  - `ts/services/backups/index.preload.ts` — Ordner-/Dateierzeugung
    (`exportPlaintext`, `exportLocalBackup`, `main.jsonl`, `metadata.json`,
    `signal-export-`/`signal-backup-`-Präfixe, Speicherplatzprüfungen).
    <https://github.com/signalapp/Signal-Desktop/blob/v8.24.1/ts/services/backups/index.preload.ts>
  - `ts/services/backups/util/localBackup.node.ts` — Sharding-Schema
    `files/<xx>/<mediaName>`.
    <https://github.com/signalapp/Signal-Desktop/blob/v8.24.1/ts/services/backups/util/localBackup.node.ts>
  - `ts/jobs/AttachmentLocalBackupManager.preload.ts` — Dateiendungs-Logik
    beim Anhang-Export.
    <https://github.com/signalapp/Signal-Desktop/blob/v8.24.1/ts/jobs/AttachmentLocalBackupManager.preload.ts>
  - `ts/util/os/promptOSAuthMain.main.ts` — OS-Authentifizierung je
    Plattform (Touch ID/Windows Hello/Polkit).
    <https://github.com/signalapp/Signal-Desktop/blob/v8.24.1/ts/util/os/promptOSAuthMain.main.ts>
  - `ts/state/ducks/backups.preload.ts` — Ablaufsteuerung inkl.
    `showExportLocationChooser` (Ordnerdialog-Titel „Anhänge speichern").
    <https://github.com/signalapp/Signal-Desktop/blob/v8.24.1/ts/state/ducks/backups.preload.ts>
  - `app/main.main.ts` — IPC-Handler `show-open-folder-dialog` (nativer
    Electron-Dialog, kein Default-Pfad).
    <https://github.com/signalapp/Signal-Desktop/blob/v8.24.1/app/main.main.ts>
  - `ts/services/backups/constants.std.ts` — `LOCAL_BACKUP_VERSION = 1`.
  - `_locales/de/messages.json` und `_locales/en/messages.json` — exakte
    UI-Texte (`icu:PlaintextExport--*`), deutsch und englisch, Tag `v8.24.1`.
  - Commit-Historie der Datei `PlaintextExportWorkflow.dom.tsx`
    (`gh api repos/signalapp/Signal-Desktop/commits?path=...`): Einführung
    durch Commit `c4378d9c` „Support for exporting chats to disk"
    (2025-11-18), verfeinert durch `89caa708` „Improvements to plaintext
    export" (2025-11-24).
    <https://github.com/signalapp/Signal-Desktop/commit/c4378d9c248f684a6c18c764fcf89995e9ff0e3d>
  - Release-Liste (`gh api repos/signalapp/Signal-Desktop/releases`): erster
    Stable-Tag mit der Datei ist `v7.82.0` (veröffentlicht 2025-12-10);
    Funktion seither unverändert bis `v8.24.1` (2026-08-20) und im
    unveröffentlichten `main` (2026-08-26) nachweisbar identisch.
- GitHub Issues (reale Nutzerberichte, zur Gegenprobe des aus dem Code
  abgeleiteten Ablaufs):
  - „Export Chat History fails" #7731 — bestätigt exakt den Menüpfad
    „Settings → Chats → Export chat history → Export" und die Checkbox
    „Include media"; dokumentiert Bugs zwischen v7.90.0–v8.11.0, seither
    laut Signal-Team behoben.
    <https://github.com/signalapp/Signal-Desktop/issues/7731>
  - „Export Chat History fails" #7873 — Beispiel für die Verwechslung mit
    der anderen (verschlüsselten) Backup-Funktion.
    <https://github.com/signalapp/Signal-Desktop/issues/7873>
- Offizielle Signal-Support-Artikel (decken nur die **andere** Funktion ab,
  hier zur Abgrenzung zitiert):
  - „Lokale Signal Desktop-Backups" (verschlüsseltes Android-Backup, 64-stelliger
    Wiederherstellungsschlüssel).
    <https://support.signal.org/hc/de/articles/10870366816410-Lokale-Signal-Desktop-Backups>
  - „Backups und Geräte-Übertragungen auf Signal" (Übersicht/Vergleichstabelle
    aller Signal-Backup-Wege).
    <https://support.signal.org/hc/de/articles/10074659364122-Backups-und-Ger%C3%A4te-%C3%9Cbertragungen-auf-Signal>
- Community-Quelle (nicht offiziell, nur zur zeitlichen Einordnung
  herangezogen, Kerninhalt durch Quellcode-Commit-Daten bestätigt):
  - aboutsignal.com, „Signal Desktop update: local backups, chat export, and
    pinned messages in the works" — nennt denselben Commit `c4378d9`, den
    auch die GitHub-Recherche unabhängig als Einführungs-Commit fand.
    <https://aboutsignal.com/de/news/signal-desktop-update-lokale-backups-chat-export-angeheftete-nachrichten/>

## Offene Punkte

- **Touch-ID-Verhalten ohne Touch-ID-Hardware auf macOS** ist nur aus dem
  Quellcode abgeleitet (kein Hardware-Check vor `promptTouchID`, jeder
  Fehler → Abbruch). Kein Support-Artikel oder Bug-Report wurde gefunden,
  der bestätigt, ob macOS-Geräte ohne Touch ID (z. B. ältere Mac minis ohne
  Magic-Keyboard-mit-Touch-ID, VMs) tatsächlich blockiert werden oder ob
  Electron/macOS intern einen Fallback bereitstellt.
- **Kein offizieller Signal-Support-Artikel** für „Export chat history"
  selbst wurde gefunden — nur In-App-Text, Quellcode und Community-Berichte.
  Sollte Signal einen solchen Artikel nachreichen, ist er gegen diese
  Recherche gegenzuprüfen.
- **Ob das Anhang-Dateinamensschema mit Erweiterung
  (`<mediaName>.<ext>`) bereits in den beiden lokal vorliegenden echten
  Backups vorliegt** (306 MB / 1,0 GB, siehe Haupt-`CLAUDE.md`), wurde nicht
  geprüft — diese Recherche hat ausschließlich den Signal-Desktop-Quellcode
  gelesen, keine lokale Datei angefasst. Vor Screenshots mit sichtbaren
  Dateinamen im `files/`-Verzeichnis: gegen einen aktuellen echten Export
  verifizieren.
- **Verhalten bei bereits vorhandenem Zielordner** (z. B. zwei Exporte
  innerhalb derselben Sekunde, oder ein Nutzer, der denselben Ordner erneut
  wählt) wurde im Code nicht bis ins Detail nachvollzogen — der
  Sekunden-genaue Zeitstempel im Ordnernamen macht Kollisionen unwahrscheinlich,
  aber nicht unmöglich geprüft.
- **Linux-Distributionsformate** (deb/rpm vs. AppImage) wurden nicht einzeln
  darauf geprüft, ob die Polkit-Policy-Datei
  (`org.signalapp.plaintext-export.policy`) in jedem Paketformat tatsächlich
  installiert wird — nur der Quellcode, der die Policy-ID referenziert,
  wurde bestätigt.
