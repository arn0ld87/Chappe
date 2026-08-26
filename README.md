# Chappe

**Signal-Backups lesbar und auswertbar machen.**

Chappe liest ein von Signal Desktop exportiertes Backup-Verzeichnis in eine SQLite-Datenbank und macht daraus etwas, mit dem man arbeiten kann: eine Volltextsuche über alle Nachrichten, Auswertungen über Jahre hinweg, Anhänge unter sprechenden Namen statt zufälliger Hashes — und eine statische HTML-Ansicht, die sich liest wie der Messenger selbst, nur offline und dauerhaft.

Benannt nach [Claude Chappe](https://de.wikipedia.org/wiki/Claude_Chappe), der 1792 den optischen Telegrafen erfand — das erste Netz, das Nachrichten über Distanz übertrug.

## Was es kann

- **Import** eines oder mehrerer Backups in eine gemeinsame Datenbank
- **Volltextsuche** über alle Nachrichten (SQLite FTS5), wahlweise wörtlich
- **Verlauf lesen** im Terminal, als Markdown oder als Klartext
- **Auswertungen**: Nachrichten pro Person, Monat, Wochentag und Stunde, häufigste Wörter und Reaktionen, Anrufbilanz, Median-Antwortzeiten
- **Anhänge exportieren** unter `Datum_Absender_Name.ext` statt Signals Zufallsnamen — als Hardlinks, also ohne zusätzlichen Speicherbedarf
- **HTML-Export**: eine eigenständige Seite pro Chat mit Bubbles, Zitaten, Reaktionen, Bildern, Videos und abspielbaren Sprachnachrichten, dazu Live-Suche und Statistikbereich. Keine externen Ressourcen, kein Server, läuft direkt vom Dateisystem
- **Freie SQL-Abfragen** gegen ein normalisiertes Schema, nur lesend

## Installation

```bash
pip install -e .
```

Keine Abhängigkeiten. Python 3.11 oder neuer, sonst nichts.

Ohne Installation geht es auch direkt aus dem Quellbaum:

```bash
PYTHONPATH=src python3 -m chappe --help
```

## Benutzung

```bash
# Backup einlesen
chappe import ~/signal-export-2026-08-26 --label privat

# Was ist drin?
chappe chats
chappe info

# Suchen
chappe search "Urlaub"                      # Volltext
chappe search "Urlaub OR Ferien" --since 2025
chappe search "@example.com" --literal      # wörtlich, ohne Tokenizer

# Lesen
chappe show --chat Anna --tail 50
chappe show --chat Anna --since 2026-03 --until 2026-04 --format md

# Auswerten
chappe stats --chat Anna

# Anhänge herausholen
chappe media --out ./medien --type image

# Archiv zum Durchblättern bauen
chappe export html --out ./archiv --media link
open ./archiv/index.html

# Eigene Fragen stellen
chappe sql "SELECT strftime('%Y-%m', sent_at/1000, 'unixepoch') AS monat,
                   COUNT(*) FROM v_messages GROUP BY monat ORDER BY monat"
```

### Mehrere Backups

Backups mehrerer Accounts können in einer Datenbank liegen. Damit man nicht jedes Mal Pfade tippt, lassen sich Quellen registrieren:

```bash
chappe sources add ~/signal-export-2026-08-26 --name privat
chappe sources scan ~/Backups          # Verzeichnis nach Exporten durchsuchen
chappe sources                         # zeigt, was registriert und was importiert ist
chappe import                          # fragt, welche Quelle geladen werden soll
```

Auswertende Befehle nehmen dann `--backup <label>`. Liegen mehrere Backups vor und ist keins gewählt, fragt Chappe nach, statt stillschweigend zu mischen — denn Backups zweier Accounts überlappen: Dieselbe Nachricht steht in beiden, einmal als gesendet, einmal als empfangen. `--all-backups` geht bewusst über alle und warnt dabei.

## Woher kommt das Backup?

Signal Desktop, Einstellungen → Chats → Backups. Chappe erwartet das entpackte Verzeichnis mit `main.jsonl` und `files/`.

## Sicherheit

**Ein Signal-Backup ist kein gewöhnliches Datenverzeichnis.** `main.jsonl` enthält im Klartext die SVR-PIN, den Profilschlüssel, den Media-Root-Key und die Identitätsschlüssel aller Kontakte. Wer ein Backup entpackt herumliegen lässt, hat mehr preisgegeben als seine Nachrichten.

Chappe entfernt dieses Schlüsselmaterial beim Import aus den gespeicherten Rohdaten. `--keep-secrets` hebt das auf und warnt dabei deutlich.

Weiter:

- `chappe sql` akzeptiert nur `SELECT`, `WITH` und `EXPLAIN` und läuft zusätzlich unter `PRAGMA query_only`.
- Der HTML-Export escapt jeden Wert aus der Datenbank. Nachrichteninhalte kommen von anderen Menschen, und die können Markup schicken.
- Die Datenbank ist unverschlüsselt. Sie enthält den vollständigen Klartext aller Chats — sie gehört dorthin, wo auch das Backup hingehört.

## Wie die Anhänge zugeordnet werden

Die Dateien unter `files/` tragen Zufallsnamen aus Signal Desktop, die keine Information enthalten. Weder der Name noch eine Ableitung aus dem Media-Root-Key führt zur zugehörigen Nachricht. Die einzige Verbindung ist der Inhalt selbst:

```
sha256(Dateiinhalt) == base64decode(attachment.pointer.locatorInfo.plaintextHash)
```

Chappe hasht deshalb beim Import jede Datei. Das ist der langsamste Teil eines Imports — und der Grund, warum die Anhänge überhaupt den richtigen Nachrichten zugeordnet werden können.

Nicht jeder Anhang liegt lokal vor: Was nie heruntergeladen wurde, existiert im Backup nur als Verweis auf Signals CDN. Chappe erfasst diese Anhänge mit Typ, Größe und Dateinamen und markiert sie in der Ausgabe als fehlend, statt sie zu verschweigen.

## Datenmodell

Elf Tabellen, zwei Views. `v_messages` und `v_chat_overview` sind für eigene Abfragen gedacht:

```sql
-- Wer schreibt wie viel?
SELECT author, COUNT(*) FROM v_messages
 WHERE chat = 'Anna' GROUP BY author;

-- Aktivität über die Jahre
SELECT substr(sent_local, 1, 7) AS monat, COUNT(*) FROM v_messages
 GROUP BY monat ORDER BY monat;

-- Die längsten Nachrichten
SELECT sent_local, author, length(body) AS len, substr(body, 1, 80)
  FROM v_messages ORDER BY len DESC LIMIT 10;
```

`chappe sql` ohne Argument listet alle Tabellen und Spalten auf.

## Entwicklung

```bash
PYTHONPATH=src python3 -m pytest tests -q
```

Die Testsuite läuft gegen ein synthetisch erzeugtes Mini-Backup im echten Signal-Format (`tests/fixtures/make_fixture.py`) — mit echten Dateien und korrekt berechneten Hashes, aber erfundenen Inhalten. Es braucht kein echtes Backup, um an Chappe zu arbeiten.

## Lizenz

MIT.

---

*Chappe steht in keiner Verbindung zur Signal Foundation oder zu Signal Messenger LLC. „Signal" ist deren Marke und wird hier ausschließlich verwendet, um das gelesene Dateiformat zu benennen.*
