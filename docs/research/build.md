# Build-Pipeline: electron-builder für drei Plattformen in GitHub Actions

Recherche zu Slice 1 aus `docs/gui-plan.md` ("Drei-Plattform-Beweis"). Bezieht
sich auf die dort festgelegte Architektur: `chappe` läuft als PyInstaller-Binary,
Kindprozess von Electron, JSON zeilenweise über stdin/stdout. Stand: 2026-08-26,
electron-builder 26.15.7 (stabil) / 27.0.0-alpha.7, PyInstaller 6.22.2.

## Abriss

Pro Plattform zwei Schritte in einem Job: erst PyInstaller baut aus
`src/chappe/__main__.py` ein eigenständiges Binary, dann electron-builder packt
Electron + Vue-Frontend + dieses Binary (als `extraResources`) zu einem
Installer. Das läuft als GitHub-Actions-Matrix über drei Runner
(`macos-latest`, `windows-latest`, `ubuntu-latest`), weil PyInstaller **nicht**
cross-kompiliert — weder über Betriebssysteme noch über CPU-Architekturen
hinweg. Signierung ist für v1 explizit aus (`docs/gui-plan.md`: "Auslieferung:
Erst unsigniert, Pipeline von Anfang an signierfähig"); die Pipeline unten ist
so gebaut, dass Signierung später nur Secrets und `if`-Bedingungen ergänzt,
keine Struktur ändert.

## Empfehlung mit Begründung

### Runner-Wahl

| Ziel | Runner | Anmerkung |
|---|---|---|
| macOS | `macos-latest` | zeigt seit kurzem auf **macOS 26, Arm64** (Apple Silicon), nicht mehr Intel — siehe Fallstricke |
| Windows | `windows-latest` | zeigt auf Windows Server 2025 |
| Linux | `ubuntu-latest` | zeigt auf Ubuntu 24.04 x64 |

Quelle für die aktuelle Runner-Zuordnung: die Label-Tabelle in
[actions/runner-images](https://github.com/actions/runner-images) (README,
Abschnitt „Available Images", abgerufen 2026-08-26). `-latest`-Labels
verschieben sich mit der Zeit — GitHub kündigt das vorher an, aber ein Pin auf
konkrete Versionen (`macos-15`, `ubuntu-24.04`) ist robuster, falls eine
zukünftige `-latest`-Verschiebung PyInstaller-Verhalten ändert (siehe
Fallstricke zu Architektur).

### Python auf jedem Runner

`actions/setup-python@v7` (aktuell, siehe
[Releases](https://github.com/actions/setup-python/releases)) auf allen drei
Runnern — es unterstützt macOS, Windows und Linux einheitlich und ist
vorinstalliert im Runner-Toolcache, dadurch schnell. `chappe` verlangt
`requires-python = ">=3.11"` (`pyproject.toml`), also `python-version: "3.12"`
oder passend zum restlichen Projekt-Stack (`CLAUDE.md`: Python 3.12).

```yaml
- uses: actions/setup-python@v7
  with:
    python-version: "3.12"
- run: python -m pip install pyinstaller
```

Kein `uv` nötig für den PyInstaller-Schritt selbst — `chappe` hat keine
Laufzeit-Abhängigkeiten (`dependencies = []` in `pyproject.toml`), PyInstaller
selbst ist eine reine Build-Abhängigkeit dieser neuen App-Schicht, nicht des
Pakets (`docs/gui-plan.md`: „PyInstaller ist Build-, keine
Laufzeit-Abhängigkeit").

### PyInstaller-Schritt

Empfehlung: **`--onedir`, nicht `--onefile`**. Begründung: `chappe` läuft als
langlebiger Sidecar-Prozess (ein Start pro App-Sitzung, nicht pro Aufruf) —
der Onefile-Overhead (Selbstentpacken in ein Temp-Verzeichnis bei *jedem*
Start) fällt also bei jedem App-Start erneut an, nicht nur einmal.
`--onedir` vermeidet das, ist einfacher zu debuggen (Inhalt liegt offen) und
ist laut PyInstaller-Doku bei macOS-Notarisierung mit Sandbox-Anforderungen
weniger fehleranfällig als `--onefile`
([PyInstaller-Doku, „Notes about specific Features"](https://pyinstaller.org/en/stable/feature-notes.html);
Praxisbericht zu Notarisierungsproblemen bei
[--onedir](https://github.com/pyinstaller/pyinstaller/issues/8927)).

```bash
pyinstaller --onedir --name chappe --paths src \
  --add-data "src/chappe/schema.sql:chappe" \
  src/chappe/__main__.py
```

Unter Windows muss der `--add-data`-Trenner `;` statt `:` sein — siehe
Fallstricke. `--paths src` spiegelt das bestehende `PYTHONPATH=src`-Setup aus
`CLAUDE.md`.

### electron-builder: extraResources

Das PyInstaller-`--onedir`-Ergebnis (`dist/chappe/` mit dem Binary und allen
`.so`/`.dll`/`.dylib`-Begleitdateien) wandert komplett in `extraResources`,
nicht nur die ausführbare Datei — sonst fehlen zur Laufzeit die
Shared-Libraries, die PyInstaller mitkopiert:

```yaml
# electron-builder.yml
extraResources:
  - from: "dist/chappe"
    to: "chappe-bin"
    filter: ["**/*"]
```

Zur Laufzeit im Hauptprozess: Pfad über `process.resourcesPath` auflösen
(`path.join(process.resourcesPath, "chappe-bin", "chappe")`, unter Windows mit
`.exe`-Endung). Quelle für Syntax und Zielverzeichnisse je Plattform
(`Contents/Resources` auf macOS, `resources/` auf Windows/Linux):
[Application Contents | electron-builder](https://www.electron.build/docs/contents/).

### electron-builder: Installer-Formate für Laien

| Plattform | Empfehlung | Begründung |
|---|---|---|
| macOS | `dmg` | Standard-Target ohnehin (zusammen mit `zip`), universell bekanntes „ins Programme-Verzeichnis ziehen" |
| Windows | `nsis` | Standard-Target von electron-builder, assistierter Installer mit vertrautem „Weiter/Weiter/Fertig"-Ablauf |
| Linux | `deb` **und** `AppImage` parallel bauen | siehe unten |

`dmg`+`zip` sind laut Doku die Default-Targets für macOS („both are required
for Squirrel.Mac auto-update") und `nsis` ist der Default für Windows
([macOS-Targets](https://www.electron.build/docs/mac/),
[Windows-Targets](https://www.electron.build/docs/win/)). Für Linux gibt es
**keinen** eindeutigen Königsweg für Laien: `AppImage` braucht keine
Installation und läuft distributionsübergreifend, verlangt vom Nutzer aber
einen manuellen `chmod +x`-Schritt nach dem Download, weil AppImages ohne
gesetztes Ausführ-Bit ausgeliefert werden — für jemanden ohne Terminal-Erfahrung
eine reale Hürde
([AppImage-Troubleshooting](https://docs.appimage.org/user-guide/troubleshooting/fuse.html)).
`deb` dagegen integriert sich nativ in Ubuntu/Debian-Paketverwaltung
(Doppelklick öffnet den Software-Installer), deckt aber nur Debian-basierte
Distributionen ab. Da Chappes Zielgruppe unklar über Distributionen verteilt
ist, beide bauen und in der Anleitung „Ubuntu/Debian: die .deb-Datei, alles
andere: die AppImage-Datei" schreiben ist der pragmatische Mittelweg.

```yaml
linux:
  target:
    - target: deb
    - target: AppImage
```

### Kompletter Workflow (Entwurf, ungesignt)

```yaml
name: build-app
on:
  push:
    branches: [main]
  workflow_dispatch:

jobs:
  build:
    strategy:
      matrix:
        os: [macos-latest, windows-latest, ubuntu-latest]
    runs-on: ${{ matrix.os }}
    defaults:
      run:
        working-directory: app
    steps:
      - uses: actions/checkout@v7

      - uses: actions/setup-python@v7
        with:
          python-version: "3.12"
      - run: python -m pip install pyinstaller
        working-directory: .
      - name: PyInstaller-Binary bauen (Unix)
        if: runner.os != 'Windows'
        run: |
          pyinstaller --onedir --name chappe --paths src \
            --add-data "src/chappe/schema.sql:chappe" \
            --distpath app/resources \
            src/chappe/__main__.py
        working-directory: .
      - name: PyInstaller-Binary bauen (Windows)
        if: runner.os == 'Windows'
        run: |
          pyinstaller --onedir --name chappe --paths src `
            --add-data "src/chappe/schema.sql;chappe" `
            --distpath app/resources `
            src/chappe/__main__.py
        working-directory: .
      - name: Ausführ-Bit setzen (Unix)
        if: runner.os != 'Windows'
        run: chmod +x app/resources/chappe/chappe
        working-directory: .

      - uses: actions/setup-node@v5
        with:
          node-version: 20
          cache: npm
          cache-dependency-path: app/package-lock.json
      - run: npm ci
      - run: npx electron-builder --publish never
        env:
          # Signing-Variablen: solange die folgenden Secrets nicht existieren,
          # sind es leere Strings — electron-builder erzeugt dann automatisch
          # unsignierte Artefakte, ohne dass sich am Workflow etwas ändert.
          CSC_LINK: ${{ secrets.MAC_CSC_LINK }}
          CSC_KEY_PASSWORD: ${{ secrets.MAC_CSC_KEY_PASSWORD }}
          APPLE_ID: ${{ secrets.APPLE_ID }}
          APPLE_APP_SPECIFIC_PASSWORD: ${{ secrets.APPLE_APP_SPECIFIC_PASSWORD }}
          APPLE_TEAM_ID: ${{ secrets.APPLE_TEAM_ID }}
          WIN_CSC_LINK: ${{ secrets.WIN_CSC_LINK }}
          WIN_CSC_KEY_PASSWORD: ${{ secrets.WIN_CSC_KEY_PASSWORD }}

      - uses: actions/upload-artifact@v7
        with:
          name: chappe-${{ matrix.os }}
          path: |
            app/dist/*.dmg
            app/dist/*.exe
            app/dist/*.deb
            app/dist/*.AppImage
          if-no-files-found: error
```

Grundgerüst (Matrix, `npx electron-builder`, kein Marketplace-„Electron
Builder Action") folgt der offiziellen Empfehlung:
[GitHub Actions CI/CD | electron-builder](https://www.electron.build/docs/features/github-actions/).
Direkter CLI-Aufruf statt dedizierter Action ist bewusst — siehe Fallstricke
zu `samuelmeuli/action-electron-builder`.

### Artefakte herunterladen

Zwei unterschiedliche Wege, je nach Zweck:

- **Interne QA / Entwickler mit Repo-Zugriff**: `actions/upload-artifact@v7`
  wie oben, Download über den Workflow-Run in der GitHub-UI (Reiter
  „Summary" → Abschnitt „Artifacts") oder per `gh run download <run-id>`.
  Wichtig für ein öffentliches Repo: Der Download-Link verlangt trotzdem ein
  eingeloggtes GitHub-Konto — auch bei öffentlichen Repos gibt es bislang
  keinen anonymen Zugriff auf Actions-Artefakte
  ([actions/upload-artifact#144](https://github.com/actions/upload-artifact/issues/144)).
  Für Laien, die Chappe nur ausprobieren wollen, ist das keine akzeptable
  Verteilung.
- **Öffentliche Auslieferung an Endanwender**: `--publish onTagOrDraft` (oder
  `always`) mit `permissions: contents: write` im Workflow hängt die
  Installer direkt als Assets an ein GitHub Release — öffentlich ohne Login
  herunterladbar. Das ist der richtige Weg für die Zielgruppe aus
  `docs/gui-plan.md` (Personen ohne technischen Hintergrund). Werte für
  `--publish`: `never` / `always` / `onTag` / `onTagOrDraft`
  ([Publish | electron-builder](https://www.electron.build/publish.html)).

## Signierfähigkeit vorbereiten, ohne sie zu aktivieren

Ziel laut Auftrag: die Pipeline so bauen, dass Signierung später nur Secrets
und zwei Schritte ist. Konkret vorzubereiten:

**macOS (Code Signing + Notarisierung):**
- Secrets anlegen (leer lassen, nur Namen reservieren): `MAC_CSC_LINK`
  (Base64 des `.p12`-Zertifikats), `MAC_CSC_KEY_PASSWORD`, `APPLE_ID`,
  `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID`. Alternative für CI, laut
  Doku empfohlen: API-Key-Variante (`APPLE_API_KEY`, `APPLE_API_KEY_ID`,
  `APPLE_API_ISSUER`) statt Passwort — kein App-spezifisches Passwort nötig,
  robuster gegen 2FA-Änderungen
  ([macOS Notarization | electron-builder](https://www.electron.build/docs/features/code-signing/notarization/)).
- In `electron-builder.yml` schon jetzt vorsehen (auch wenn `CSC_LINK` leer
  ist, macht das keinen Unterschied im unsignierten Fall):
  ```yaml
  mac:
    hardenedRuntime: true
    notarize: false   # später auf true / entfernen, sobald Secrets existieren
  ```
- **Wichtig für später:** Sobald signiert wird, muss das eingebettete
  PyInstaller-Binary mitsigniert werden, nicht nur die Electron-App selbst —
  siehe Fallstricke.

**Windows (Code Signing):**
- Secrets reservieren: `WIN_CSC_LINK`, `WIN_CSC_KEY_PASSWORD`. Die klassische
  PFX-Zertifikat-Variante ist stabil und ohne Version-Vorbehalt nutzbar
  ([Code Signing for Windows | electron-builder](https://www.electron.build/docs/features/code-signing/code-signing-win/)).
- Für die Zukunft im Hinterkopf behalten, nicht jetzt einbauen: Microsofts
  **Azure Trusted Signing** (Cloud-Signing, kein lokales Zertifikat,
  günstiger als klassische OV-Zertifikate) wird in electron-builder 27 über
  `win.sign: { type: "azure" }` konfiguriert. Diese v27-Linie ist aktuell
  Alpha (`27.0.0-alpha.7`, siehe
  [Releases](https://github.com/electron-userland/electron-builder/releases));
  auf der stabilen 26.x-Linie bleibt es bei `WIN_CSC_LINK`. Kein Grund, jetzt
  schon auf v27 zu pinnen — nur ein Hinweis, dass sich das
  Windows-Signing-Config-Schema in absehbarer Zeit ändert.

**Allgemein:** `forceCodeSigning: true` **nicht** setzen, solange unsigniert
gebaut wird — dieser Schalter lässt den Build hart fehlschlagen statt
stillschweigend unsigniert zu bauen, genau das Gegenteil vom gewünschten
Übergang.

## Fallstricke

- **PyInstaller kompiliert nicht plattformübergreifend — und auch nicht
  architekturübergreifend.** Ein auf Linux gebautes Binary läuft nicht unter
  Windows/macOS, das ist Kernaussage der PyInstaller-Doku und mehrfach in
  Community-Threads bestätigt
  ([Cross-Compilation-Diskussion](https://groups.google.com/g/pyinstaller/c/KISZP5sHCWg)).
  Deshalb zwingend drei Runner, kein „auf Linux für alle drei bauen".

- **`macos-latest` zeigt inzwischen auf Apple Silicon (arm64), nicht mehr
  Intel** ([actions/runner-images README](https://github.com/actions/runner-images),
  Label-Tabelle, Stand 2026-08-26). PyInstaller baut ohne explizites
  `--target-arch` immer für die Architektur des Runners
  ([PyInstaller Feature-Notes](https://pyinstaller.org/en/stable/feature-notes.html)).
  Ergebnis: Das auf `macos-latest` gebaute `chappe`-Binary läuft **nicht**
  auf Intel-Macs. `actions/setup-python` liefert auf arm64-Runnern
  Standardmäßig kein universal2-Python
  ([actions/runner-images#4133](https://github.com/actions/virtual-environments/issues/4133)),
  daher hilft ein einfaches `--target-arch universal2` allein nicht — dafür
  bräuchte es den offiziellen universal2-Installer von python.org statt
  `actions/setup-python`. Weil `chappe` aber keine Drittanbieter-C-Extensions
  hat (`dependencies = []`), ist ein universal2-Build hier tatsächlich
  realistisch machbar, nur eben nicht der Standardweg über
  `actions/setup-python`. Bis das geklärt ist: entweder nur arm64-Macs
  unterstützen und das im Onboarding sagen, oder zusätzlich einen
  `macos-13`/`macos-15-large` (Intel-)Runner in die Matrix aufnehmen und zwei
  getrennte `mac`-Artefakte (x64/arm64) statt eines `universal`-Targets
  bauen — `mac.target` selbst wählt kein Architektur, das läuft separat über
  `--x64`/`--arm64`/`--universal`
  ([macOS-Targets | electron-builder](https://www.electron.build/docs/mac/)).

- **`schema.sql` muss PyInstaller explizit über `--add-data` mitgegeben
  werden**, sonst schlägt `Path(__file__).with_name("schema.sql")` in
  `importer.py:16` zur Laufzeit im gebauten Binary fehl — genau das Risiko,
  das `docs/gui-plan.md` bei Slice 1 schon benennt. Das Trennzeichen im
  `--add-data`-Argument unterscheidet sich je Plattform: `:` auf macOS/Linux,
  `;` auf Windows — ein reines Copy-Paste des Kommandos zwischen den drei
  Matrix-Jobs bricht auf Windows
  ([PyInstaller-Doku, `--add-data`](https://pyinstaller.org/en/stable/usage.html)).

- **electron-builder erhält beim Kopieren nach `extraResources` nicht
  zuverlässig das Ausführ-Bit** des PyInstaller-Binaries auf macOS/Linux —
  mehrfach dokumentiertes Verhalten, Workaround ist ein expliziter
  `chmod +x`-Schritt oder ein `afterPack`-Hook
  ([electron-builder#8006](https://github.com/electron-userland/electron-builder/issues/8006),
  [electron-builder#777](https://github.com/electron-userland/electron-builder/issues/777)).
  Symptom, falls übersehen: Die fertige App startet den Sidecar nicht,
  „Permission denied", nur auf macOS/Linux, nicht auf Windows — schwer zu
  reproduzieren, wenn man lokal auf demselben Rechner testet, auf dem man
  gebaut hat.

- **PyInstaller-Binaries lösen häufig Antivirus-/Windows-Defender-Fehlalarme
  aus**, besonders unsigniert — bekanntes, seit Jahren offenes Problem, nicht
  spezifisch für Chappe. Codesigning reduziert das deutlich, weil AV-Hersteller
  eher ein Zertifikat als einzelne Datei-Hashes whitelisten
  ([PyInstaller-FAQ zu Antivirus](https://www.pythonguis.com/faq/problems-with-antivirus-software-and-pyinstaller/),
  [pyinstaller#6754](https://github.com/pyinstaller/pyinstaller/issues/6754)).
  Relevant für die Erwartungshaltung an die unsignierte v1: SmartScreen-
  Warnung und ggf. Defender-Meldung sind zu erwarten, nicht nur bei der
  `.exe` der Electron-App, sondern potenziell auch beim eingebetteten
  `chappe.exe`.

- **Für die macOS-Notarisierung müssen alle Binaries im App-Bundle signiert
  sein, nicht nur die Haupt-App** — das eingebettete PyInstaller-Binary in
  `Contents/Resources/chappe-bin/` zählt dazu. electron-builder signiert
  Executables in `extraResources` nicht automatisch zuverlässig mit; im
  Zweifel ist `mac.sign.binaries` mit dem konkreten Pfad zum PyInstaller-
  Binary anzugeben
  ([Beispiel aus der Praxis](https://gist.github.com/txoof/0636835d3cc65245c6288b2374799c43),
  [macOS Notarization | electron-builder](https://www.electron.build/docs/features/code-signing/notarization/)).
  Betrifft v1 noch nicht (unsigniert), ist aber genau die Stelle, an der die
  „signierfähig vorbereitete" Pipeline in Slice 11 zuerst bricht, wenn sie
  vergessen wird.

- **Die Marketplace-Action `samuelmeuli/action-electron-builder` ist
  archiviert** (letzter Push 2024-05-26, GitHub-API bestätigt
  `archived: true`) — nicht als Basis für einen neuen Workflow verwenden.
  Direkter `npx electron-builder`-Aufruf, wie oben, ist der von der
  electron-builder-Doku selbst gezeigte Weg
  ([GitHub Actions CI/CD | electron-builder](https://www.electron.build/docs/features/github-actions/)).

- **AppImage-Bauen kann je nach `toolsets.appimage`-Pin FUSE2 verlangen.**
  Ubuntu 24.04 (= aktuelles `ubuntu-latest`) hat `libfuse2` in `libfuse2t64`
  umbenannt; ältere/gepinnte AppImage-Toolsets von electron-builder brechen
  deshalb ab, sofern nicht explizit `libfuse2t64` nachinstalliert wird. Der
  aktuelle Default-Runtime von electron-builder ist laut Projekt-Aussagen
  FUSE-frei, aber das hängt an der genauen `toolsets.appimage`-Version — im
  Zweifel im CI-Log prüfen, ob der Build tatsächlich ohne FUSE durchläuft,
  statt sich blind auf „ist doch neu genug" zu verlassen
  ([libfuse2t64-Hinweis](https://github.com/LibrePCB/LibrePCB/issues/980),
  [electron-builder#9598](https://github.com/electron-userland/electron-builder/issues/9598)).

- **AppImage-Dateien sind nach dem Download nicht ausführbar** — der Nutzer
  muss selbst `chmod +x` setzen oder im Dateimanager „Als Programm
  ausführen" aktivieren. Für die in `docs/gui-plan.md` beschriebene
  Zielgruppe („Person ohne technischen Hintergrund") ist das eine reale
  Hürde, die in der Onboarding-Anleitung explizit adressiert werden muss,
  wenn AppImage als Linux-Format angeboten wird
  ([AppImage-Troubleshooting-Doku](https://docs.appimage.org/user-guide/troubleshooting/fuse.html)).

- **GitHub-Actions-Artefakte (`upload-artifact`) sind für anonyme Endnutzer
  keine geeignete Verteilung**, selbst bei einem öffentlichen Repo — der
  Download verlangt ein eingeloggtes GitHub-Konto
  ([actions/upload-artifact#144](https://github.com/actions/upload-artifact/issues/144)).
  Für „hier ist der Installer, probier's aus" ist ein GitHub-Release-Asset
  über `--publish` der richtige Weg, nicht der Artifact-Tab.

## Quellen

- [Multi Platform Build | electron-builder](https://www.electron.build/docs/features/multi-platform-build/)
- [GitHub Actions CI/CD | electron-builder](https://www.electron.build/docs/features/github-actions/)
- [Application Contents (extraResources) | electron-builder](https://www.electron.build/docs/contents/)
- [Code Signing | electron-builder](https://www.electron.build/docs/features/code-signing/)
- [Code Signing for Windows | electron-builder](https://www.electron.build/docs/features/code-signing/code-signing-win/)
- [macOS Notarization | electron-builder](https://www.electron.build/docs/features/code-signing/notarization/)
- [macOS-Targets | electron-builder](https://www.electron.build/docs/mac/)
- [Windows-Targets | electron-builder](https://www.electron.build/docs/win/)
- [Linux-Targets | electron-builder](https://www.electron.build/docs/linux/)
- [Publish | electron-builder](https://www.electron.build/publish.html)
- [electron-builder Releases (GitHub API, 26.15.7 stabil / 27.0.0-alpha.7)](https://github.com/electron-userland/electron-builder/releases)
- [PyInstaller-Doku: Using PyInstaller (`--add-data`)](https://pyinstaller.org/en/stable/usage.html)
- [PyInstaller: Notes about specific Features (`--target-arch`, universal2)](https://pyinstaller.org/en/stable/feature-notes.html)
- [PyInstaller Cross-Compilation-Diskussion (Google Group)](https://groups.google.com/g/pyinstaller/c/KISZP5sHCWg)
- [PyInstaller Releases (GitHub API, 6.22.2 aktuell)](https://github.com/pyinstaller/pyinstaller/releases)
- [PyInstaller-FAQ: Antivirus-Fehlalarme](https://www.pythonguis.com/faq/problems-with-antivirus-software-and-pyinstaller/)
- [pyinstaller/pyinstaller#6754 – AV-Fehlalarm bei `--onefile`](https://github.com/pyinstaller/pyinstaller/issues/6754)
- [pyinstaller/pyinstaller#8927 – Notarisierungsprobleme mit `--onedir`](https://github.com/pyinstaller/pyinstaller/issues/8927)
- [actions/runner-images – README, Label-Tabelle](https://github.com/actions/runner-images)
- [actions/runner-images – macOS 26 arm64 Readme](https://github.com/actions/runner-images/blob/main/images/macos/macos-26-arm64-Readme.md)
- [actions/virtual-environments#4133 – kein universal2-Python in setup-python](https://github.com/actions/virtual-environments/issues/4133)
- [actions/setup-python Releases (GitHub API, v7.0.0 aktuell)](https://github.com/actions/setup-python/releases)
- [actions/setup-node Releases (GitHub API, v7.0.0 aktuell)](https://github.com/actions/setup-node/releases)
- [actions/checkout Releases (GitHub API, v7.0.1 aktuell)](https://github.com/actions/checkout/releases)
- [actions/upload-artifact Releases (GitHub API, v7.0.1 aktuell)](https://github.com/actions/upload-artifact/releases)
- [actions/download-artifact Releases (GitHub API, v8.0.1 aktuell)](https://github.com/actions/download-artifact/releases)
- [actions/upload-artifact#144 – Login-Pflicht bei öffentlichen Artefakten](https://github.com/actions/upload-artifact/issues/144)
- [samuelmeuli/action-electron-builder (GitHub API: `archived: true`)](https://github.com/samuelmeuli/action-electron-builder)
- [electron-builder#8006 – Ausführ-Bit geht bei extraResources verloren](https://github.com/electron-userland/electron-builder/issues/8006)
- [electron-builder#777 – Permission denied nach Packen](https://github.com/electron-userland/electron-builder/issues/777)
- [electron-builder#9598 – AppImage-Build bricht auf neueren Ubuntu-Versionen](https://github.com/electron-userland/electron-builder/issues/9598)
- [LibrePCB#980 – `libfuse2` → `libfuse2t64` auf Ubuntu 22.04+](https://github.com/LibrePCB/LibrePCB/issues/980)
- [AppImage-Troubleshooting: FUSE und Ausführ-Bit](https://docs.appimage.org/user-guide/troubleshooting/fuse.html)
- [OS X Code Signing Pyinstaller (Praxisbeispiel `mac.sign.binaries`)](https://gist.github.com/txoof/0636835d3cc65245c6288b2374799c43)
- Lokale Quellen: `docs/gui-plan.md` (Architekturentscheidungen, Slice 1),
  `pyproject.toml`, `src/chappe/importer.py:16` (`SCHEMA`-Pfad), Kommando
  `gh api repos/<org>/<repo>/releases` (Versions-/Datumsverifikation, direkt
  ausgeführt am 2026-08-26).

## Offene Punkte

- **Ob es bei einem `arm64`-only-macOS-Build für v1 bleibt, oder ob von
  Anfang an ein zweiter Intel-Runner in die Matrix soll**, ist nicht
  recherchierbar, sondern eine Produktentscheidung — abhängig davon, wie
  viele potenzielle Nutzer noch Intel-Macs haben. `docs/gui-plan.md` trifft
  dazu keine Aussage.
- **Kein aktuelles, primärquellen-belegtes Beispiel für `mac.sign.binaries`
  mit einem PyInstaller-`--onedir`-Verzeichnis** (nur mit Onefile-Binaries)
  gefunden. Für Slice 11 lohnt ein kleiner Testlauf mit echtem
  Developer-ID-Zertifikat, bevor man sich auf die automatische
  Tiefensignierung von electron-builder verlässt.
- **Ob `libfuse2t64` auf dem aktuellen `ubuntu-latest`-Image für AppImage-
  Builds tatsächlich noch gebraucht wird**, konnte nicht abschließend anhand
  der electron-builder-Doku selbst verifiziert werden (nur über
  GitHub-Issues, die je nach `toolsets.appimage`-Version widersprüchliche
  Aussagen zulassen) — ein tatsächlicher CI-Lauf mit `--linux AppImage`
  klärt das schneller als weitere Doku-Recherche.
- **Ob electron-builder 27 (Alpha) bis Slice 11 stabil ist** und damit das
  neue `win.sign`-Schema für Azure Trusted Signing genutzt werden könnte,
  lässt sich heute nicht vorhersagen — Empfehlung bleibt, für v1 auf der
  26.x-Linie zu bauen und diese Frage bei Slice 11 neu zu stellen.
- **Kein Preis-/Voraussetzungsvergleich für Azure Trusted Signing selbst**
  wurde vertieft (Verfügbarkeit für Einzelentwickler in Deutschland/EU) —
  laut einer Suchtreffer-Zusammenfassung nur für US/Kanada-Organisationen
  bzw. -Einzelentwickler verfügbar (Stand Oktober 2025 laut Quelle), aber
  nicht an einer electron-builder- oder Microsoft-Primärquelle
  gegengeprüft. Vor einer Entscheidung für Azure Trusted Signing sollte das
  direkt bei Microsoft verifiziert werden.
