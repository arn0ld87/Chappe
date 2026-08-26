# Electron-Härtung und Medien-Auslieferung über eigenes Protokoll

Recherchestand: 2026-08-26, gegen Electron **v44.0.0** (aktueller Stand der
offiziellen Doku unter `electronjs.org/docs/latest`, verifiziert über die
Release-Liste auf GitHub). Bezug: `docs/gui-plan.md`, Abschnitt „Nicht
verhandelbar" — Electron-Härtung ist dort bereits als Anforderung gesetzt,
diese Recherche liefert die Begründung und die konkreten Bausteine dafür.

## Abriss

Chappe liest `main.jsonl`-Dateien, die im Klartext `svrPin`, `profileKey`,
`mediaRootBackupKey` und die `identityKey` aller Kontakte enthalten (siehe
`CLAUDE.md`, Abschnitt „Sicherheit"). Die geplante Electron-App zeigt diese
Daten in einem Renderer-Prozess an, der HTML/CSS/JS lädt — der historische
Haupt-Angriffsweg für Electron-Apps ist genau dieser Renderer: eine XSS-Lücke
(hier: eine geschickt formatierte Chatnachricht, die der Chatpartner
kontrolliert) plus fehlende Isolation ergibt Remote Code Execution auf dem
Rechner, der die Backups liest. Die vier Härtungsfelder aus dem Auftrag —
`contextIsolation`, `sandbox`, `nodeIntegration`, CSP — sind seit Electron 12
(contextIsolation) bzw. 20 (sandbox) bzw. 5 (nodeIntegration)
**Standardverhalten**, nicht mehr manuell zu aktivieren. Die Aufgabe für
Chappe ist also nicht „Defaults einschalten", sondern **explizit machen, was
sonst durch eine einzige falsche Zeile leise wieder verschwindet** — und die
eine Stelle, die Electron nicht für einen erledigt, sauber selbst bauen: die
Medienauslieferung über ein eigenes Protokoll.

Zweiter Befund, weniger erwartet: Video-/Audio-Streaming über
`protocol.handle` ist in der Community seit über zwei Jahren ein aktiver
Schmerzpunkt mit einer bestätigten Chromium-Regression, die erst im August
2025 gefixt wurde und laut den Entwicklern jederzeit durch Upstream-Arbeit an
Chromiums `MultiBufferNeverDefer` wieder aufbrechen kann. Das ist keine
Nebensache für Slice 6 (Medien) — es ist der Teil des Plans mit dem größten
Risiko, dass „funktioniert bei mir" nicht „funktioniert auf der
Ziel-Electron-Version" bedeutet.

## Empfehlung

### 1. Renderer-Härtung: contextIsolation, sandbox, nodeIntegration explizit setzen

Trotz Defaults **explizit** in jedem `BrowserWindow` angeben — als
dokumentierte Absicht, nicht als stillschweigend geerbtes Verhalten, das ein
späterer Refactor kippen kann:

```js
// app/main/window.js
const mainWindow = new BrowserWindow({
  webPreferences: {
    contextIsolation: true,   // Default seit v12, hier explizit
    sandbox: true,            // Default seit v20, hier explizit
    nodeIntegration: false,   // Default seit v5, hier explizit
    preload: path.join(__dirname, '../preload/index.js'),
    webSecurity: true,        // niemals abschalten
  }
})
```

`sandbox: true` bedeutet: Im Renderer läuft **kein** Node.js-Environment.
Das Preload-Skript bekommt einen gepolyfillten `require`, der nur ein
begrenztes Subset zulässt (`electron`-Renderer-Module wie `contextBridge`,
`ipcRenderer`, `webUtils`; dazu `events`, `timers`, `url`,
`node:`-Importe) — keine beliebigen Node-Module. Wichtig für Chappe: **Wer
`nodeIntegration: true` setzt, schaltet damit automatisch auch die Sandbox
ab** — die beiden Flags sind gekoppelt, ein einzelner Fehlgriff hebt zwei
Schutzschichten gleichzeitig auf.
[Quelle: Sandbox-Doku](https://www.electronjs.org/docs/latest/tutorial/sandbox)

### 2. contextBridge: eine Methode pro IPC-Kanal, kein Pass-Through

Die Doku nennt explizit ein **unsicheres** Muster, das genau danach aussieht,
was man versehentlich schreibt, wenn man „schnell mal was durchreichen"
will:

```js
// ❌ Unsicher — Renderer kann beliebige IPC-Nachrichten senden
contextBridge.exposeInMainWorld('myAPI', {
  send: ipcRenderer.send
})
```

Richtig: pro RPC-Methode ein benanntes Feld, das intern genau einen
`ipcRenderer.invoke`-Aufruf macht. Für Chappes RPC-Schicht
(`src/chappe/rpc.py`, siehe `docs/gui-plan.md` Slice 2) heißt das:

```js
// app/preload/index.js
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('chappeAPI', {
  listChats: (backupId) => ipcRenderer.invoke('rpc:list_chats', backupId),
  transcript: (chatId, opts) => ipcRenderer.invoke('rpc:transcript', chatId, opts),
  search: (query, opts) => ipcRenderer.invoke('rpc:search', query, opts),
  // ... eine Zeile je RPC-Methode aus rpc.py, nichts Generisches
})
```

Auf der Hauptprozess-Seite jede `ipcMain.handle`-Registrierung mit
Sender-Validierung, nicht nur mit der Methodenlogik:

```js
// app/main/ipc.js
function validateSender(frame) {
  // In einer gepackten App lädt das Fenster ausschließlich vom eigenen
  // privilegierten Schema — jede andere Origin ist verdächtig.
  return frame.url.startsWith('chappe://app/')
}

ipcMain.handle('rpc:list_chats', (event, backupId) => {
  if (!validateSender(event.senderFrame)) return null
  return rpc.listChats(backupId)
})
```

Der Grund für die Sender-Prüfung: **Alle** Web-Frames — auch iframes und in
manchen Szenarien Kindfenster — können IPC-Nachrichten an den Hauptprozess
schicken. Ohne Prüfung reicht eine einzelne eingebettete fremde Ressource,
um privilegierte Handler aufzurufen.
[Quelle: Context-Isolation-Doku](https://www.electronjs.org/docs/latest/tutorial/context-isolation),
[Security-Checkliste Punkt 17](https://www.electronjs.org/docs/latest/tutorial/security#17-validate-the-sender-of-all-ipc-messages)

### 3. CSP für eine App ohne jede externe Ressource

Chappe lädt nichts von außen — kein CDN, keine Web-Fonts, kein Tracking,
keine Remote-API. Das erlaubt die striktestmögliche Policy: bei Null
anfangen und nur das freigeben, was die App tatsächlich lädt.

```
default-src 'none';
script-src 'self';
style-src 'self';
img-src 'self' chappe-media:;
media-src 'self' chappe-media:;
connect-src 'self';
font-src 'self';
```

Zwei Wege, die Policy zu setzen — und der Unterschied ist für Chappe
relevant, weil die App laut Plan **nicht** über `file://` lädt, sondern über
ein eigenes Schema (Punkt 4):

- **HTTP-Header** (bevorzugt): Wenn das Hauptfenster-HTML selbst über
  `protocol.handle` ausgeliefert wird, lässt sich der
  `Content-Security-Policy`-Header direkt auf die `Response` setzen — kein
  Umweg nötig.
- **`<meta http-equiv="Content-Security-Policy">`**: Nötig, wenn HTML über
  `file://` geladen wird, weil dort kein HTTP-Header-Mechanismus greift. Für
  Chappe nur relevant, falls Slice 0/1 zunächst noch mit `loadFile()` statt
  eigenem Schema arbeiten.

Alternative für app-weite Durchsetzung unabhängig vom Ladeweg:
`session.defaultSession.webRequest.onHeadersReceived()` — setzt den Header
für jede Antwort in der Session, greift aber erst nach `app.whenReady()`.

[Quelle: Security-Doku, Abschnitt 7](https://www.electronjs.org/docs/latest/tutorial/security#7-define-a-content-security-policy),
[Quelle: IPC-Tutorial, Beispiel-`index.html`](https://www.electronjs.org/docs/latest/tutorial/ipc)

### 4. Eigenes Protokoll für Medien statt `file://`

**Warum überhaupt ein eigenes Schema:** `file://` bekommt in Electron mehr
Rechte als in einem normalen Browser — eine Seite, die von `file://` lädt,
hat unilateralen Zugriff auf jede Datei auf der Maschine. Bei Chappe, wo eine
XSS-Lücke über eine bösartige Chatnachricht theoretisch denkbar ist, wäre das
der direkte Weg zu beliebigem Dateizugriff. Offizielle Empfehlung: Punkt 18
der Security-Checkliste, „Avoid usage of the `file://` protocol and prefer
usage of custom protocols".
[Quelle](https://www.electronjs.org/docs/latest/tutorial/security#18-avoid-usage-of-the-file-protocol-and-prefer-usage-of-custom-protocols)

**Schritt 1 — Schema als privilegiert registrieren**, vor `app.whenReady()`,
und **nur einmal aufrufbar**:

```js
// app/main/protocol.js — vor app.whenReady()
protocol.registerSchemesAsPrivileged([
  {
    scheme: 'chappe-media',
    privileges: {
      standard: true,        // relative URLs, FileSystem API funktionieren
      secure: true,
      supportFetchAPI: true,
      stream: true,          // <video>/<audio> erwarten sonst gepufferte Antworten
      bypassCSP: false,      // NICHT setzen — CSP soll für dieses Schema gelten
      corsEnabled: false,
    }
  }
])
```

`bypassCSP: true` ist eine verlockende Abkürzung aus mehreren
Community-Beispielen, aber ein Kompromiss: Sie nimmt das Schema **komplett**
aus der CSP-Prüfung heraus statt nur eine Ausnahme zu definieren. Für Chappe
richtiger: `media-src 'self' chappe-media:` explizit in der Policy (Punkt 3)
statt Bypass.

**Schritt 2 — Handler mit Bezeichner-Auflösung ausschließlich über die
Datenbank.** Der entscheidende Designpunkt für Pfad-Traversal-Sicherheit bei
Chappe: Der Bezeichner, den der Renderer im Protokoll-Pfad sieht, darf
**niemals** direkt zu einem Dateisystempfad werden. Er ist ein reiner
Lookup-Key gegen `media_files`, nicht Teil eines Pfads:

```js
// app/main/protocol.js — nach app.whenReady()
const HEX64 = /^[0-9a-f]{64}$/

protocol.handle('chappe-media', async (request) => {
  const { pathname } = new URL(request.url) // chappe-media://media/<hash>
  const hash = pathname.replace(/^\/+/, '')

  // 1. Formatprüfung, bevor überhaupt eine Query läuft — kein Traversal-
  //    Zeichen kommt je in die Nähe von path.join.
  if (!HEX64.test(hash)) {
    return new Response('bad request', { status: 400 })
  }

  // 2. Auflösung ausschließlich über die DB: backup_id + local_path kommen
  //    aus media_files, lokal geschrieben von index_media() beim Import —
  //    niemals vom Request-String übernommen.
  const row = db.get(
    'SELECT b.source_path, m.local_path FROM media_files m ' +
    'JOIN backups b ON b.id = m.backup_id WHERE m.plaintext_hash = ?',
    [hash]
  )
  if (!row || !row.local_path) {
    return new Response('not found', { status: 404 })
  }

  // 3. Containment-Check nach dem Vorbild der offiziellen protocol.handle-
  //    Doku — auch wenn local_path aus der eigenen DB kommt: verteidigt
  //    zusätzlich gegen Symlink-Eskapaden und schützt vor genau der Klasse
  //    Bug, die laut CLAUDE.md den Medien-Export schon einmal lahmgelegt
  //    hat (eingeschobenes .parent, falsch zusammengesetzter Pfad).
  const resolved = path.resolve(row.source_path, row.local_path)
  const relative = path.relative(row.source_path, resolved)
  const isSafe = relative && !relative.startsWith('..') && !path.isAbsolute(relative)
  if (!isSafe) {
    return new Response('bad', { status: 400 })
  }

  return serveWithRange(resolved, request)
})
```

`path.join(row.source_path, row.local_path)` **darf nicht** `.parent`
einschieben — exakt die Invariante aus `CLAUDE.md` zu `index_media()`, jetzt
im Protokoll-Handler statt im HTML-Export.

**Schritt 3 — Range-Requests für Video/Audio manuell implementieren, nicht
auf `net.fetch()` allein verlassen.** Das offizielle Doku-Beispiel

```js
protocol.handle('atom', (request) => {
  const filePath = request.url.slice('atom://'.length)
  return net.fetch(url.pathToFileURL(path.join(__dirname, filePath)).toString())
})
```

funktioniert für statische Assets (HTML, CSS, Bilder), ist aber für Video-
Seeking **nicht** die von der Community empfohlene Lösung — siehe
Fallstricke unten. Belastbares Muster, destilliert aus dem langen
GitHub-Issue-Thread (mehrfach unabhängig konvergiert, u. a. von
`dev-techfago`, `agsimmons`, `zoy-l`):

```js
function serveWithRange(filePath, request) {
  const stat = fs.statSync(filePath)
  const fileSize = stat.size
  const range = request.headers.get('range')
  const mimeType = lookupMime(filePath) // MIME-Zuordnung aus model.py wiederverwenden

  if (!range) {
    return new Response(nodeStreamToWeb(fs.createReadStream(filePath)), {
      status: 200,
      headers: {
        'Content-Type': mimeType,
        'Content-Length': String(fileSize),
        'Accept-Ranges': 'bytes',
      }
    })
  }

  const match = range.match(/bytes=(\d*)-(\d*)/)
  if (!match) return new Response(null, { status: 416 })
  const start = match[1] ? parseInt(match[1], 10) : 0
  const end = match[2] ? parseInt(match[2], 10) : fileSize - 1
  if (start >= fileSize || end >= fileSize || start > end) {
    return new Response(null, { status: 416, headers: { 'Content-Range': `bytes */${fileSize}` } })
  }

  const stream = fs.createReadStream(filePath, { start, end })
  return new Response(nodeStreamToWeb(stream), {
    status: 206,
    headers: {
      'Content-Type': mimeType,
      'Content-Range': `bytes ${start}-${end}/${fileSize}`,
      'Content-Length': String(end - start + 1),
      'Accept-Ranges': 'bytes',
    }
  })
}
```

`nodeStreamToWeb` ist Node-eigen (`stream.Readable.toWeb`, seit Node 17) und
wandelt den `fs.createReadStream`-Rückgabewert in einen Web-`ReadableStream`,
den das `Response`-Konstrukt akzeptiert — ein direktes Node-`Readable` als
Response-Body führt bei mehreren Nutzern im Issue-Thread zu Problemen
(„Response does not appear to be constructed correctly").

[Quelle: protocol-API-Doku mit Original-Beispiel](https://www.electronjs.org/docs/latest/api/protocol),
[Quelle: CustomScheme-Struktur, `stream`-Flag](https://www.electronjs.org/docs/latest/api/structures/custom-scheme),
[Quelle: GitHub-Issue #38749, Kommentare von dev-techfago, agsimmons, zoy-l](https://github.com/electron/electron/issues/38749)

### 5. Fuses als zusätzliche Schicht, nicht als Ersatz

Fuses sind Package-Time-Bits, die nach dem Code-Signing vom Betriebssystem
gegen Rückflippen geschützt werden (Gatekeeper/AppLocker). Für Chappe
relevant, weil die App sensible Kontodaten verarbeitet und keinen Bedarf für
diese Features hat:

| Fuse | Empfehlung für Chappe | Begründung |
|---|---|---|
| `runAsNode` | deaktivieren | Verhindert `ELECTRON_RUN_AS_NODE`-basierte Code-Injection (siehe Fallstricke, Discord-CVE) |
| `nodeCliInspect` | deaktivieren | Verhindert `--inspect`/`--inspect-brk` von außen |
| `onlyLoadAppFromAsar` | aktivieren | Erzwingt Laden ausschließlich aus `app.asar`, kein Fallback auf lose Dateien |
| `embeddedAsarIntegrityValidation` | aktivieren | Validiert `app.asar`-Inhalt beim Laden |
| `grantFileProtocolExtraPrivileges` | deaktivieren | Chappe lädt nicht von `file://` (Punkt 4) — die Zusatzrechte werden nicht gebraucht |

Gesetzt über `@electron/fuses` im Build-Skript, nach dem Electron-Packaging
und vor dem Code-Signing.
[Quelle: Fuses-Doku](https://www.electronjs.org/docs/latest/tutorial/fuses)

## Fallstricke

1. **Video-Scrubbing-Regression in Electron 37.0.0–37.2.x.** Zwischen den
   Nightly-Builds `37.0.0-nightly.20250422` (funktioniert) und `.20250423`
   (kaputt) brach `video.seekable` mit `protocol.handle` — bestätigt per
   Bisect von `@agsimmons`, verursacht durch einen Chromium-Bump
   (`dd03cceda038…`). Gefixt durch
   [PR #47703](https://github.com/electron/electron/pull/47703) („fix: video
   scrubbing on playback"), gemerged am 2025-08-05, in v37 ausgeliefert
   (bestätigt von `danielweck` am 2025-08-11). Die PR-Beschreibung selbst
   warnt: „There is currently upstream work around MultiBufferNeverDefer v2
   … future upstream CLs may result in the patch needing to change again in
   the near future." **Konsequenz für Chappe:** Die Electron-Version für die
   App nicht ungeprüft auf `latest` pinnen, sondern Video-Seeking bei jedem
   Major-Update explizit gegen ein großes Testvideo verifizieren, bevor die
   Version hochgezogen wird — das ist keine hypothetische Regression,
   sondern bereits zweimal passiert (2023 initial, 2025 durch Chromium-Bump
   wieder).
   [Quelle](https://github.com/electron/electron/issues/38749)

2. **`net.fetch(pathToFileURL(...))` allein liefert kein verlässliches
   Range-Handling.** Das offizielle Doku-Beispiel für `protocol.handle`
   fetcht die komplette Datei ohne den eingehenden `Range`-Header
   weiterzureichen. Mehrere Entwickler im Issue-Thread haben das über zwei
   Jahre hinweg mit unterschiedlichen Varianten versucht — Header
   durchreichen (`net.fetch(url, { headers: { Range: ... } })`), volle Datei
   als Blob laden (`createObjectURL`), reiner `createReadStream()` ohne
   Header — mit wechselndem Erfolg **je nach Plattform und Electron-Version**
   (funktioniert auf macOS, nicht auf Windows; funktioniert in v35, nicht in
   v36/v37; s.o.). Die einzige Lösung, die mehrfach unabhängig als robust
   bestätigt wurde, ist der manuelle 206-Response-Bau aus Schritt 4.3 oben.
   Mehrere Maintainer-nahe Stimmen (`6zz`, `Enlumis`) fragen im Thread
   explizit, warum das nicht als Utility-Funktion in Electron selbst
   existiert — aktuell (Stand August 2026) existiert sie nicht.

3. **Blob-basierter Workaround belastet den Speicher.** Der von
   `Amstramgram75` vorgeschlagene Ansatz (`audio.src =
   URL.createObjectURL(await (await fetch(url)).blob())`) funktioniert für
   mittelgroße Dateien (dort: 300–600 MB MP3), lädt aber die **komplette**
   Datei in den Speicher, bevor überhaupt Wiedergabe beginnt — für Chappes
   Anwendungsfall (Videos aus einem 1-GB-Signal-Backup) nicht geeignet, auch
   wenn er in manchen Threads als „einfachste Lösung" auftaucht.

4. **`registerSchemesAsPrivileged` ist ein Einmal-Aufruf vor `ready`.** Zweite
   Aufrufe werden ignoriert bzw. schlagen fehl; ein Hot-Reload des
   Hauptprozesses während der Entwicklung, der diese Zeile erneut ausführt,
   maskiert Konfigurationsfehler statt sie zu zeigen. In der Praxis heißt
   das: Änderungen an den Privilegien brauchen einen vollständigen
   Neustart der Electron-App, nicht nur einen Renderer-Reload.

5. **`nodeIntegration: true` schaltet die Sandbox implizit mit ab** — ein
   einzelnes falsch gesetztes Flag hebt zwei Schutzschichten gleichzeitig
   auf, ohne dass das an der Stelle sichtbar wird, an der `sandbox` gesetzt
   wurde.

6. **CSP über `<meta>`-Tag vs. Header ist kein reiner Stilunterschied.** Für
   `file://`-geladene Seiten bleibt nur der Meta-Tag, weil dort kein
   HTTP-Header-Mechanismus greift — das ist ein Electron/Chromium-
   Implementierungsdetail, keine freie Wahl. Wird Chappes Renderer-HTML über
   das eigene Schema ausgeliefert (empfohlen, Punkt 4), steht der
   Header-Weg offen und sollte auch genutzt werden.

7. **Reale CVEs zeigen: `contextIsolation` + `sandbox` allein reichen nicht,
   wenn Electron selbst einen Bug hat.** Bei Element (Matrix) war
   `contextIsolation: true` und `app.enableSandbox()` bereits gesetzt — die
   Lücke (CVE-2022-23597, ausgenutzt über CVE-2022-29247) kam daher, dass
   `nodeIntegrationInSubFrames: false` in einer Electron-Version nicht
   durchgesetzt wurde und Kind-Frames trotzdem `ipcRenderer`-Zugriff
   bekamen. Konsequenz: Sender-Validierung auf jedem `ipcMain.handle`
   (Punkt 2) ist keine optionale Zusatzmaßnahme, sondern die Verteidigungs-
   schicht, die genau diese Klasse Framework-Bug abfängt. Bei Discord
   (CVE-2024-23739, CVSS 9.8) war die aktivierte `ELECTRON_RUN_AS_NODE`-
   Umgebungsvariable der Hebel — der Fix waren exakt die in Punkt 5
   empfohlenen Fuses.
   [Quelle: SecureLayer7-Blogpost mit CVE-Aufschlüsselung](https://blog.securelayer7.net/electron-app-security-risks-part-2/)
   — Sekundärquelle, CVE-Nummern dort nicht gegen die offizielle NVD-Einträge
   gegengeprüft (siehe Offene Punkte).

8. **PyInstaller-Sidecar ist ein zweiter IPC-Kanal, den Electrons
   Sicherheitsmodell nicht abdeckt.** Laut `docs/gui-plan.md` spricht
   Electron mit dem `chappe`-Backend zeilenweise JSON über stdin/stdout.
   Alles, was oben zu IPC-Sender-Validierung gesagt wird, betrifft
   ausschließlich den `ipcMain`/`ipcRenderer`-Kanal zwischen Electron-
   Prozessen — die stdin/stdout-Verbindung zum Python-Kindprozess braucht
   eigene Eingabevalidierung (Methodenname, Argumenttypen, Größenlimits für
   die JSON-Zeilen) im `rpc.py`-Adapter selbst. Dazu liefert diese Recherche
   keine Electron-spezifischen Quellen, weil es kein Electron-Feature ist.

## Quellen

- [Electron Security-Doku (v44)](https://www.electronjs.org/docs/latest/tutorial/security) — vollständige 20-Punkte-Checkliste, CSP, IPC-Sender-Validierung, `file://`-Vermeidung
- [Electron Context-Isolation-Doku](https://www.electronjs.org/docs/latest/tutorial/context-isolation) — contextBridge-Muster, sicheres vs. unsicheres Beispiel
- [Electron Process-Sandboxing-Doku](https://www.electronjs.org/docs/latest/tutorial/sandbox) — Sandbox-Verhalten, Kopplung an nodeIntegration, Preload-Polyfills
- [Electron `protocol`-API-Doku](https://www.electronjs.org/docs/latest/api/protocol) — `protocol.handle`, `registerSchemesAsPrivileged`, Original-Beispiel mit Path-Traversal-Schutz
- [Electron `CustomScheme`-Struktur](https://www.electronjs.org/docs/latest/api/structures/custom-scheme) — alle Privilegien-Flags inkl. `stream`
- [Electron `net`-API-Doku](https://www.electronjs.org/docs/latest/api/net) — `net.fetch`, `bypassCustomProtocolHandlers`
- [Electron IPC-Tutorial](https://www.electronjs.org/docs/latest/tutorial/ipc) — Pattern 1 (renderer→main), CSP-Meta-Tag-Beispiel
- [Electron Fuses-Doku](https://www.electronjs.org/docs/latest/tutorial/fuses) — alle Fuses, Begründung, Signierungs-Interaktion
- [GitHub-Issue electron/electron#38749 „video files not seekable with protocol.handle"](https://github.com/electron/electron/issues/38749) — 69 Kommentare, Juni 2023 bis Juni 2026, mehrere konvergierende Workarounds, bestätigte Regression und Fix
- [GitHub-Issue electron/electron#47661 „Can't scrub the video served over protocol handler"](https://github.com/electron/electron/issues/47661) — Bisect der v37-Regression
- [GitHub-PR electron/electron#47703 „fix: video scrubbing on playback"](https://github.com/electron/electron/pull/47703) — Fix, Hinweis auf zukünftiges Chromium-Risiko
- [GitHub-Issue electron/electron#41986](https://github.com/electron/electron/issues/41986) — verwandtes `protocol.handle`-Problem bei Audiodateien
- [SecureLayer7: „Electron App Security Risks Part 2" (Discord/Element-CVEs)](https://blog.securelayer7.net/electron-app-security-risks-part-2/) — konkrete Exploit-Ketten, CVE-2020-15174, CVE-2021-21220, CVE-2022-23597, CVE-2022-29247, CVE-2022-36059, CVE-2024-23739

## Offene Punkte

- **CVE-Nummern der SecureLayer7-Quelle sind nicht gegen NVD/offizielle
  Advisories gegengeprüft.** Die Zuordnung Exploit → CVE-Nummer stammt aus
  einer Sekundärquelle; für eine belastbare Aussage („genau dieser CVE traf
  genau dieses Verhalten") müsste man die jeweiligen NVD-Einträge oder
  Electron-Release-Notes einzeln nachschlagen. Für den Zweck dieser
  Recherche (Beleg, dass reale Exploit-Ketten existieren und welche
  Schutzschicht sie ausgehebelt haben) reicht die Quelle, für eine
  Sicherheitsmeldung oder einen Blogpost mit Zitat nicht.
- **Ob `stream: true` in `registerSchemesAsPrivileged` tatsächlich einen
  messbaren Unterschied macht**, ist in der Community umstritten —
  `agsimmons` berichtet im Issue-Thread explizit, keinen Unterschied
  festgestellt zu haben, während die offizielle Doku es als nötig für
  `<video>`/`<audio>` beschreibt. Keine Quelle gefunden, die das technisch
  auflöst; die Empfehlung oben folgt der offiziellen Doku, nicht der
  widersprüchlichen Praxiserfahrung.
  Nicht Bestandteil dieser Recherche.
- **Windows-Defender-Fehlalarm bei `registerFileProtocol`** (erwähnt von
  `vincaslt` und `hiaaryan` im Issue-Thread) ist nicht unabhängig
  verifiziert — als weiteres Argument gegen die veraltete API notiert, aber
  nicht als belastbare Tatsache in die Empfehlung eingeflossen.
- **Ob die im August 2025 gefixte Regression in der zum Zeitpunkt des
  Chappe-Electron-Pinnings aktuellen Version (v44 oder neuer) noch intakt
  ist**, wurde nicht gegen ein Test-Fiddle auf v44 verifiziert — nur die
  Fix-PR und ihre Bestätigung in v37 sind belegt. Vor Slice 6 („Medien") ein
  eigener Praxistest mit einer großen Videodatei auf der tatsächlich
  gepinnten Electron-Version ist angeraten, unabhängig von dieser Recherche.
- **PyInstaller-Sidecar-Härtung** (Eingabevalidierung auf dem
  stdin/stdout-JSON-Kanal zu `chappe rpc`) ist nicht recherchiert — liegt
  außerhalb dessen, was Electrons eigenes Sicherheitsmodell abdeckt, und
  bräuchte eine eigene Recherche zur sicheren Gestaltung von
  Kindprozess-Protokollen.
