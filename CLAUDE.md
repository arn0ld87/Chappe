# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Was das Projekt ist

**Chappe** liest von Signal Desktop exportierte Backup-Verzeichnisse (`main.jsonl` + `files/`) in eine normalisierte SQLite-Datenbank und macht sie durchsuchbar, auswertbar und lesbar — als CLI, als Markdown/CSV/JSON und als statische HTML-Chatansicht mit eingebetteten Medien.

Benannt nach Claude Chappe, dem Erfinder des optischen Telegrafen. **Der Name enthält bewusst kein „Signal":** Das Projekt soll veröffentlicht werden, und die Signal Foundation geht gegen Marken-Nutzung in Projektnamen vor. Wer hier umbenennt oder ein Untermodul benennt, hält sich daran. „Signal" als Beschreibung des eingelesenen Formats („Signal-Backup", „Signal Desktop") ist davon unberührt und korrekt.

Reine Standardbibliothek, **keine Laufzeit-Abhängigkeiten**. Das ist eine bewusste Entscheidung, kein Zufall — wer eine Bibliothek hinzufügen will, braucht einen guten Grund.

## Befehle

```bash
export PYTHONPATH=src          # oder: pip install -e .

python3 -m chappe import <backup-dir> --label alex   # Backup einlesen
python3 -m chappe sources                            # verwaltete Quellen
python3 -m chappe chats                              # Chatübersicht
python3 -m chappe search "Begriff" --chat Anna       # Volltextsuche (FTS5)
python3 -m chappe search "Begr" --literal            # wörtliche Teilstringsuche
python3 -m chappe show --chat Anna --tail 50         # Verlauf
python3 -m chappe stats --backup alex                # Auswertung
python3 -m chappe media --out ./medien               # Anhänge mit Namen
python3 -m chappe export html --out ./archiv --media link
python3 -m chappe sql "SELECT chat, COUNT(*) FROM v_messages GROUP BY chat"
```

Tests:
```bash
PYTHONPATH=src python3 -m pytest tests -q     # 49 Tests, laufen in unter 1 s
PYTHONPATH=src python3 -m pytest tests/test_importer.py::test_report_counts -q
```

Die Tests laufen gegen ein **synthetisches Mini-Backup**, das `tests/fixtures/make_fixture.py` erzeugt — echtes Signal-Format, erfundene Daten, echte Dateien mit korrekt berechnetem `plaintextHash`. Kein echtes Backup gehört je in die Testsuite oder ins Repo.

## Die beiden echten Backups

Lokal liegen zwei vollständige Backups **zweier verschiedener Signal-Accounts**, die gemeinsam in einer DB auswertbar sein sollen: eines mit 9.330 Nachrichten (306 MB), eines mit 39.274 Nachrichten (1,0 GB). Ihre Pfade stehen **absichtlich nicht in diesem Repo** — sie sind private Daten und leben in `~/.config/chappe/sources.json`, verwaltet über `chappe sources add`. Wer sie hier oder im Quellcode einträgt, macht das Projekt unveröffentlichbar.

Der zweite Import dauert wegen des Medien-Hashings etwa vier Minuten. Das ist erwartet, nicht kaputt.

### Zwei Accounts heißt: zwei Perspektiven auf teils dieselben Ereignisse

Die Backups überlappen — dieselben Chats liegen zweimal vor, aus je einer Perspektive. Für jede Auswertung über beide hinweg gilt:

- **Dieselbe Nachricht existiert zweimal mit vertauschter `direction`.** Was im einen Backup `outgoing` ist, ist im anderen `incoming`. Wer über beide Backups zählt, zählt gemeinsame Nachrichten doppelt. Der CLI-Schalter `--all-backups` tut das bewusst und warnt deshalb.
- **Der belastbare Dedup-Schlüssel ist `(sent_at, aci des Autors)`** — nicht `chat_id`, nicht `rid`, nicht der Anzeigename.
- **Ein Mensch ist mehrere `recipients`-Zeilen**, pro Backup eine, teils mit anderem `kind` (in einem Backup `self`, im anderen `contact`). Backup-übergreifend identifizieren nur `aci`, `pni`, `e164`.
- **Es gibt zwei „Ich"** — `backups.self_rid`/`self_name` unterscheiden sich je Backup.
- **Dieselbe Mediendatei hat in beiden Backups denselben `plaintext_hash`.** `media_files` ist über `UNIQUE (backup_id, plaintext_hash)` bewusst pro Backup gehalten; für eine backup-übergreifende Deduplizierung beim Export ist der Hash der Schlüssel.

`v_messages` und `v_chat_overview` filtern **nicht** nach `backup_id` und führen nichts zusammen — sie liefern beide Accounts nebeneinander, inklusive Doppelungen. Das ist so gebaut, nicht versehentlich; Zusammenführung baut man darüber, nicht hinein.

## Architektur

Vier Schichten, klar getrennt, Abhängigkeiten nur nach unten:

**`model.py`** — reine Deutung des Backup-JSON, kennt keine Datenbank. Alles, was „was bedeutet dieses Feld" heißt: Namensauflösung, Nachrichtenarten, deutsche Textlabels, MIME-Zuordnung, Secret-Filter. Importiert nichts aus dem restlichen Paket; diese Richtung muss so bleiben.

**`importer.py`** — der einzige Schreiber ins Schema. `Importer.run()` liest `main.jsonl` vollständig in Listen und fügt in Abhängigkeitsreihenfolge ein, weil `chatItem` auf `recipient` und `chat` verweist. Zwei In-Memory-Maps (`rid_to_id`, `cid_to_id`) übersetzen Backup-IDs in Zeilen-IDs.

**`query.py` / `media.py`** — lesen ausschließlich. `query.py` ist die einzige Stelle, an der Filterlogik definiert wird.

**`cli.py` / `render/`** — Oberfläche. `render/html.py` und `render/markdown.py` bauen aus `query`-Ergebnissen Dateien.

### Invariante 1: Medienbindung läuft ausschließlich über den Hash

Die Dateinamen unter `files/` sind Zufallswerte aus Signal Desktop und tragen **keine** Information. Weder der Dateiname noch eine Ableitung aus dem `mediaRootBackupKey` führt zum Ziel. Die einzige Verbindung zwischen Datei und Nachricht ist:

```
sha256(Dateiinhalt) == base64decode(attachment.pointer.locatorInfo.plaintextHash)
```

`index_media()` hasht deshalb jede Datei im Backup. Das ist der teuerste Schritt jedes Imports und der Grund für die Laufzeit beim großen Backup.

`index_media()` gibt Pfade **relativ zum Backup-Verzeichnis** zurück (`files/…`), und `backups.source_path` **ist** das Backup-Verzeichnis. Wer diese Pfade wieder zusammensetzt, darf kein `.parent` einschieben — genau dieser Fehler hat den Medien-Export schon einmal vollständig lahmgelegt, ohne dass eine Exception geflogen wäre.

Nicht jeder referenzierte Anhang liegt lokal vor: Anhänge ohne `wasDownloaded` existieren nur als CDN-Pointer. Im kleinen Backup sind es 548 von 1.050. `local_path IS NULL` ist deshalb Normalfall, kein Fehler.

### Invariante 2: Chats werden über IDs gefiltert, nie über Namensteile

`query.resolve_chat_ids()` löst eine Chatbezeichnung zu Zeilen-IDs auf, **exakter Name gewinnt vor Teiltreffer**. `_filters()` filtert dann über `m.chat_id IN (…)`.

Der Grund ist konkret: Ein Chat „Alex" ist Teilstring von „Alexander Schneider" und „Alexander Zietlow". Die frühere `c.name LIKE '%…%'`-Logik lieferte für `--chat Alex` **21.198 statt 11.397 Nachrichten** — drei Chats stillschweigend zu einem vermischt, ohne Fehlermeldung. Wer neue Abfragen schreibt: `chat_id` nehmen, wenn er bekannt ist, sonst `chat` an `_filters` geben und nie selbst ein `LIKE` auf `c.name` bauen. `query.build_filter(conn, **filters)` ist der öffentliche Zugang für eigenes SQL.

### Weitere Invarianten des Schemas

- **Mehrere Backups koexistieren** in einer DB, getrennt über `backups.id`. Jede Abfrage ohne `backup_id`-Filter mischt Perspektiven. `ON DELETE CASCADE` hängt an allen Ästen, ein Re-Import löscht also nur die eine Backup-Zeile.
- **Nachrichtenrevisionen** sind Selbstreferenzen: `revision_of` zeigt auf die aktuelle Fassung, `revision_index = 0` ist die älteste. Beide Views filtern `WHERE revision_of IS NULL`; jede neue Auswertung muss das ebenfalls tun, sonst zählt sie bearbeitete Nachrichten mehrfach. `ImportReport` zählt Revisionen aus demselben Grund getrennt.
- **FTS5** (`messages_fts`) ist external-content über `messages.body`, gepflegt von drei Triggern. Nach strukturellen Eingriffen per Bulk-SQL `INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')` nachziehen.
- **Zitatauflösung** läuft zweistufig am Ende des Imports (`_resolve_quotes`), weil ein Zitat auf eine später in der Datei stehende Nachricht zeigen kann: erst exakt über `(chat_id, author_id, sent_at)`, dann als Rückfall über `(chat_id, sent_at)`. Unaufgelöste Zitate behalten `target_message_id = NULL` und bleiben über `quotes.text` lesbar.
- **Geheimnisse** werden aus dem gespeicherten `raw`-JSON entfernt (`model.SECRET_KEYS` / `strip_secrets`), sofern nicht `keep_secrets=True`. Neue Signal-Formatversionen können neue Schlüsselfelder einführen — beim Anheben der Formatunterstützung `SECRET_KEYS` mitprüfen.

### Nachrichtenarten

`model.MESSAGE_BODIES` ist die geordnete Liste der Body-Varianten, die ein `chatItem` tragen kann; `message_kind()` nimmt die erste zutreffende. `update` wird in `_insert_item` nachträglich zu `call` umgedeutet, wenn `individualCall`/`groupCall` vorliegt — `messages.kind` kennt deshalb ein `call`, das es in `MESSAGE_BODIES` nicht gibt. Eine neue Nachrichtenart braucht drei Stellen: `MESSAGE_BODIES`, den `body`-Zweig in `_insert_item`, und den Kommentar an `messages.kind` in `schema.sql`.

`subkind` trägt bei Systemnachrichten den Signal-Enum-Wert; `model.UPDATE_TEXT` und `CALL_TEXT` übersetzen ihn nach Deutsch, mit Rückfall auf den Rohwert statt einer Exception.

## Sicherheit

`main.jsonl` enthält im Klartext die `svrPin`, den `profileKey`, den `mediaRootBackupKey` und die `identityKey` aller Kontakte. Ein Backup-Verzeichnis ist damit hochsensibel.

Der Import entfernt diese Felder standardmäßig aus dem gespeicherten `raw`-JSON. `--keep-secrets` hebt das auf und warnt dabei — dieser Schalter darf nie zum Default werden, und die Tests prüfen beide Richtungen.

`chappe sql` ist auf `SELECT`/`WITH`/`EXPLAIN` beschränkt und setzt zusätzlich `PRAGMA query_only`. Der HTML-Export escapt jeden Textwert aus der Datenbank — der Inhalt sind fremde Nachrichten, und ein Chatpartner kann Markup geschickt haben.

## Sprache

Code-Kommentare, Docstrings, Hilfetexte, Reportausgaben und die deutschen Labels in `model.py` sind Deutsch, mit korrekten Umlauten. Bezeichner sind Englisch. Das durchhalten.
