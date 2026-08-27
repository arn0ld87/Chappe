# Design-Tokens für Vue-CSS und Python-HTML-Export

Recherchestand: 2026-08-26. Bezieht sich auf den aktuellen `render/html.py`
(gelesen für diese Recherche, Stand HEAD des Repos) sowie auf das öffentliche
DTCG-/Style-Dictionary-Ökosystem im August 2026.

## Abriss

`render/html.py` hat heute schon fast eine Token-Struktur, nur fest verdrahtet:
ein `_CSS`-String (Zeilen 441–555) mit einem `:root`-Block für Light Mode und
einem `@media (prefers-color-scheme: dark) { :root { … } }`-Block für Dark
Mode, beide mit denselben Custom-Property-Namen (`--bg`, `--bg-panel`,
`--text`, `--bubble-in`, `--bubble-out`, `--accent`, `--shadow`, …). Das ist
im Kern bereits ein Light/Dark-Tokenpaar — es fehlt nur die externe Quelle und
der zweite Konsument.

Für das geplante Vue-Frontend soll dieselbe Quelle beide Seiten speisen, ohne
dass der Python-Export zur Laufzeit Node braucht. Das Repo hat noch keinen
`frontend/`- oder `package.json`-Ordner (Stand dieser Recherche) — die
Vue/Electron-Seite ist Neuland, es gibt also keinen bestehenden Tokenbestand,
den man ablösen müsste.

Der Tokenwelt-Standard ist seit Kurzem stabil: Die Design Tokens Community
Group (DTCG, W3C Community Group) hat am 28. Oktober 2025 die erste
produktionsreife Fassung ihrer Spezifikation veröffentlicht, **2025.10**, in
drei Modulen — Format, Color, Resolver
([designtokens.org/tr/2025.10](https://www.designtokens.org/tr/2025.10/)).
Style Dictionary v4 unterstützt das Format nativ, Terrazzo gilt als die
vollständigere Referenzimplementierung (`@terrazzo/cli`,
[terrazzo.app/docs](https://terrazzo.app/docs/)). Beide sind reine
Node/npm-Werkzeuge — es gibt (Stand dieser Recherche) keine etablierte
Python-Bibliothek, die DTCG-JSON parst.

## Empfehlung

**Eigenbau: eine schlanke, reine-Stdlib-Python-Funktion, die eine
DTCG-inspirierte (nicht vollspezifikationskonforme) JSON-Tokendatei liest und
daraus CSS erzeugt — kein Style Dictionary, kein Terrazzo, kein Node in der
Toolchain.**

Begründung:

1. **Die Projektphilosophie verlangt einen guten Grund für jede
   Abhängigkeit.** Style Dictionary/Terrazzo wären zwar nur `devDependencies`
   (Node), nicht Laufzeitabhängigkeiten von `chappe` — das verletzt die
   Stdlib-Regel für den Python-Export formal nicht. Aber sie machen Node zum
   Pflichtwerkzeug für *jeden*, der Tokens ändert, und ihre eigentliche Stärke
   — Multi-Plattform-Transforms nach iOS/Swift, Android/Kotlin, Compose — wird
   hier nicht gebraucht. Chappe hat genau zwei Konsumenten, beide CSS
   (Vite/Vue und statisches HTML). Das ist die Aufgabe, für die man keinen
   Build-Tool-Unterbau mit eigenem Plugin-Ökosystem braucht.
2. **Der Umfang ist klein genug für Eigenbau.** Farben, Abstände, Radien,
   Schatten, Schriften, je light/dark — das sind ein paar Dutzend Werte, keine
   verschachtelten Theme-Vererbungsketten. Eine Resolver-Funktion, die
   `{farbe.oberflaeche.basis}`-Aliase auflöst und `$value`/`$type` liest, ist
   in gut 100 Zeilen Python fertig; DTCG-Vollspektrum (Resolver-Module mit
   Modifier-Kontexten, Farbraum-Interpolation, Mathe-Ausdrücken in Werten)
   bringt hier keinen Mehrwert.
3. **Eine Quelle, ein Generator, zwei Ausgabedateien** vermeidet
   Versionsdrift zwischen einer Node- und einer Python-Toolchain für dieselbe
   Aufgabe.

### Konkreter Aufbau

```
design/tokens.json                  # Quelle: light + dark, DTCG-artig
tools/build_tokens.py               # Stdlib-Generator, kein Paket-Import nötig
frontend/src/styles/tokens.css      # generiert, von Vite als CSS importiert
src/chappe/render/_tokens.css       # generiert, als Package-Data ausgeliefert
```

`design/tokens.json` (Ausschnitt, minimal, kein voller DTCG-Funktionsumfang):

```json
{
  "farbe": {
    "oberflaeche": {
      "basis": { "$type": "color", "$value": { "light": "#f2f2f5", "dark": "#101013" } },
      "panel":  { "$type": "color", "$value": { "light": "#ffffff", "dark": "#1a1a1f" } }
    },
    "text": {
      "basis":  { "$type": "color", "$value": { "light": "#1b1b1f", "dark": "#e7e7ec" } }
    },
    "akzent": { "$type": "color", "$value": { "light": "#2563eb", "dark": "#6ea8fe" } }
  },
  "radius": {
    "bubble": { "$type": "dimension", "$value": "0.7rem" }
  },
  "schatten": {
    "bubble": {
      "$type": "shadow",
      "$value": {
        "light": "0 1px 2px rgba(0,0,0,.08)",
        "dark":  "0 1px 2px rgba(0,0,0,.4)"
      }
    }
  }
}
```

Das ist bewusst *kein* volles DTCG-Dokument (kein `$schema`, keine
Resolver-Modifier-Syntax für „light"/„dark" als eigene Kontext-Objekte) —
sondern eine eigene, an DTCG angelehnte Konvention mit einem `light`/`dark`-
Paar direkt im `$value`. Das hält den Generator auf Stdlib-Niveau: kein
JSON-Schema-Validator, kein Resolver-Modul nötig, um zwei Modi
auseinanderzuhalten.

`tools/build_tokens.py` (Gerüst, reine Stdlib):

```python
import json
import re
from pathlib import Path

ALIAS_RE = re.compile(r"\{([\w.]+)\}")


def _flatten(node: dict, prefix: str = "") -> dict:
    """DTCG-artiges Token-JSON zu {css-name: {"light": .., "dark": ..}} abflachen."""
    out: dict[str, dict] = {}
    for key, value in node.items():
        path = f"{prefix}-{key}" if prefix else key
        if isinstance(value, dict) and "$value" in value:
            v = value["$value"]
            out[path] = v if isinstance(v, dict) else {"light": v, "dark": v}
        elif isinstance(value, dict):
            out.update(_flatten(value, path))
    return out


def _resolve_aliases(tokens: dict) -> dict:
    def resolve(val: str) -> str:
        def repl(m: re.Match) -> str:
            alias_path = m.group(1).replace(".", "-")
            return tokens[alias_path]["light"] if alias_path in tokens else m.group(0)
        return ALIAS_RE.sub(repl, val)

    return {
        name: {mode: resolve(v) if isinstance(v, str) else v for mode, v in modes.items()}
        for name, modes in tokens.items()
    }


def build_css(tokens_path: Path) -> str:
    raw = json.loads(tokens_path.read_text(encoding="utf-8"))
    flat = _resolve_aliases(_flatten(raw))
    light = "\n".join(f"  --{k}: {v['light']};" for k, v in flat.items())
    dark = "\n".join(f"  --{k}: {v['dark']};" for k, v in flat.items())
    return (
        f":root {{\n{light}\n}}\n"
        f"@media (prefers-color-scheme: dark) {{\n  :root {{\n{dark}\n  }}\n}}\n"
    )


if __name__ == "__main__":
    css = build_css(Path("design/tokens.json"))
    Path("frontend/src/styles/tokens.css").write_text(css, encoding="utf-8")
    Path("src/chappe/render/_tokens.css").write_text(css, encoding="utf-8")
```

Das Skript läuft mit `python3 tools/build_tokens.py`, keine
`npm install` nötig, kein Netzzugriff, keine Bibliothek außer `json`/`re`/
`pathlib`. Es wird bei jeder Tokenänderung von Hand oder per Pre-Commit-Hook
aufgerufen und schreibt in beide Zielorte — `frontend/src/styles/tokens.css`
für Vite/Vue-Import, `src/chappe/render/_tokens.css` als **generierte,
committete** Datei, die zur Laufzeit von `chappe export html` nur noch
gelesen wird:

```python
# render/html.py – statt des hartcodierten _CSS-Strings
from importlib import resources

_CSS = resources.files("chappe.render").joinpath("_tokens.css").read_text(encoding="utf-8") + _CSS_STATIC_REST
```

`_tokens.css` muss dafür als Package-Data deklariert werden
(`pyproject.toml`, `[tool.setuptools.package-data]` bzw. bei
`hatchling`/`pdm` das entsprechende Äquivalent) — sonst landet die Datei nicht
im gebauten Wheel/sdist und `importlib.resources` findet zur Laufzeit nichts
(„die Ressourcen-API kann keine Datei lesen, die das Build-Backend
weggelassen hat" — [Scientific Python Development
Guide](https://learn.scientific-python.org/development/patterns/data-files/)).
Layout-CSS (Bubbles, Header, Suche — alles, was nicht Farbe/Radius/Schatten
ist) bleibt sinnvollerweise als statischer String in `html.py`, nur der
Token-Block wandert raus.

### Zwei Wege, Light/Dark in CSS zu erzeugen

**A — klassisch, `@media (prefers-color-scheme: dark)`** (das, was
`render/html.py` heute schon macht): zwei Blöcke, maximale Kompatibilität,
funktioniert in jedem Browser der letzten zehn Jahre. Für den statischen
HTML-Export — Archivdateien, die auch in fünf Jahren noch in irgendeinem
Browser aufgehen sollen — die robustere Wahl.

**B — modern, `light-dark()`**: eine CSS-Farbfunktion, die zwei Werte nimmt
und den zur aktiven `color-scheme` passenden zurückgibt, ohne Media Query:

```css
:root { color-scheme: light dark; }
:root { --bg: light-dark(#f2f2f5, #101013); }
```

`color-scheme: light dark` muss gesetzt sein, sonst schaltet `light-dark()`
nicht um. Seit Mai 2024 in aktuellen Browserversionen verfügbar, „Baseline
Widely Available" wird laut MDN/web-features für den 2026-11-13 erwartet,
Supportquote 2026 bei rund 95 % (MDN, mit Warnung „Fallbacks für ältere
Geräte" —
[developer.mozilla.org/…/light-dark](https://developer.mozilla.org/en-US/docs/Web/CSS/color_value/light-dark);
Support-Tabelle: [caniuse.com/mdn-css_types_color_light-dark](https://caniuse.com/mdn-css_types_color_light-dark)).
Für das Vue/Electron-Frontend, das in einer kontrollierten, aktuellen
Chromium-Runtime läuft, ist B die kürzere, wartungsärmere Variante — ein Wert
pro Token statt zwei Blöcke. Für den Python-Export würde ich vorerst bei A
bleiben und B erst übernehmen, wenn der `light-dark()`-Baseline-Status
erreicht ist; das ist eine Abwägung, keine harte Notwendigkeit — der
Generator kann beide Formate aus derselben `tokens.json` erzeugen, das ist
nur eine andere `format_css()`-Funktion.

### Style Dictionary/Terrazzo vs. Eigenbau — wann was

| | Eigenbau (empfohlen) | Style Dictionary v4 | Terrazzo |
|---|---|---|---|
| Laufzeit-Tool | Python stdlib | Node ≥ 18 ([Migration Guide](https://styledictionary.com/versions/v4/migration/)) | Node/npm (`@terrazzo/cli`) |
| DTCG-Format | eigene, angelehnte Teilmenge | nativ ab v4, offizielle Konvertierung | vollständigster DTCG-Support, inkl. Resolver Module ab 2.0 geplant |
| Multi-Plattform (iOS/Android/…) | nein, nicht gebraucht | ja, Kernfeature | ja |
| Light/Dark out of the box | ja, per Konstruktion | nein — braucht Custom Format oder `$mods`-Konvention ([alwaystwisted.com, Teil 7](https://www.alwaystwisted.com/articles/a-design-tokens-workflow-part-7)) | „Resolvers & Theming" als eigenes Doku-Kapitel, Details nicht in dieser Recherche verifiziert |
| Neue Node-Abhängigkeit im Repo | nein | ja (devDependency) | ja (devDependency) |
| Sinnvoll, wenn … | genau diese zwei CSS-Konsumenten | später eine native Mobile-App mit eigenem Tokenformat dazukommt | dasselbe, plus volle Spec-Treue gewünscht ist |

Wenn Chappe später tatsächlich eine native Mobile-Ausgabe bekommt, ist das der
Punkt, an dem sich Style Dictionary lohnt — für zwei CSS-Ausgaben ist es
Overhead.

## Ästhetik: warm-taktil im Dark Mode

Kernaussage aus mehreren unabhängigen Quellen: **Schatten funktionieren im
Dunkeln anders, nicht schlechter — sie brauchen ein anderes Mittel.**

- **Schattenschichtung (Light Mode).** Die verbreitete Technik (Josh Comeau,
  „Designing Beautiful Shadows in CSS") stapelt 5–6 `box-shadow`-Layer mit
  unterschiedlichem Versatz, Unschärfe und leicht farbig getöntem Schwarz
  statt eines einzelnen harten Schattens — das Ergebnis wirkt „integriert"
  statt aufgesetzt. Tool dazu: [Shadow Palette
  Generator](https://www.joshwcomeau.com/shadow-palette/), Artikel:
  [joshwcomeau.com/css/designing-shadows](https://www.joshwcomeau.com/css/designing-shadows/).
  Das ist reines CSS, mehrere `box-shadow`-Werte kommagetrennt in einem
  Custom-Property-Token je Elevation-Stufe — trivial aus `tokens.json`
  generierbar (ein `$type: "shadow"`-Token mit Array-`$value`).
- **Oberflächenaufhellung statt Schatten (Dark Mode).** Der Konsens
  (Atlassian, Material Design, mehrere UI-Guides): Auf dunklem Grund gibt es
  keinen wahrnehmbaren Kontrast zwischen dunklem Schatten und dunklem
  Hintergrund — Schatten „lesen" dort nicht. Atlassian Design System löst das
  über eigene `elevation.surface.*`-Tokens, die bei erhöhten Flächen heller
  werden, *zusätzlich* zu (nicht statt) einem eigenen
  `elevation.shadow.*`-Token — die Doku betont ausdrücklich: „Always pair
  elevation.surface.raised with elevation.shadow.raised"
  ([atlassian.design/foundations/elevation](https://atlassian.design/foundations/elevation)).
  Material Design nennt dasselbe Prinzip „tonal elevation": höhere Flächen
  bekommen eine hellere Tonstufe der Basisfarbe
  ([m3.material.io/styles/elevation](https://m3.material.io/styles/elevation) —
  siehe „Offene Punkte", die genauen Prozentwerte konnte ich in dieser
  Recherche nicht primärquellen-sicher belegen). Ein Community-Guide nennt als
  Faustregel „4–5 % Aufhellung pro Elevation-Stufe" und rät zusätzlich, die
  Basisfarbe nicht reines Schwarz zu wählen (`#1E1F22` statt `#000000` als
  Beispiel), weil reines Schwarz bei langer Betrachtung überkontrastiert und
  ermüdet
  ([uxcel.com/blog/mastering-elevation-for-dark-ui](https://uxcel.com/blog/mastering-elevation-for-dark-ui-a-comprehensive-guide-342)).
  **Praktisch für Chappe:** Statt fixer Hex-Werte je Elevation-Stufe in
  `tokens.json` lässt sich das in reinem CSS aus einem einzigen
  Oberflächen-Token ableiten:
  ```css
  --surface-0: #101013;
  --surface-1: color-mix(in oklch, var(--surface-0), white 5%);
  --surface-2: color-mix(in oklch, var(--surface-0), white 9%);
  ```
  `color-mix(in oklch, …)` mischt perzeptuell gleichmäßig (kein
  „unerwartetes" Aufhellen wie bei `hsl()`/Sass-`darken()`) und ist reines
  CSS ohne Build-Zeit-Berechnung —
  [evilmartians.com/chronicles/oklch-in-css](https://evilmartians.com/chronicles/oklch-in-css-why-quit-rgb-hsl),
  [moderncsstools.com/guides/modern-colors](https://moderncsstools.com/guides/modern-colors/).
  Das reduziert `tokens.json` auf eine Basisfarbe pro Modus statt einer
  Werteliste pro Elevation-Stufe — weniger Redundanz, ein Ableitungsschritt
  weniger, den man von Hand pflegen müsste.
- **Randlichter / „Rim Light".** Für taktile Wärme im Dunkeln: ein feiner,
  halbtransparenter heller Rand statt (oder zusätzlich zu) einem Schatten —
  simuliert Streiflicht von vorn/oben auf einer erhöhten Fläche. Praktisch als
  `inset`-Box-Shadow, keine zusätzliche Ebene nötig:
  ```css
  --rim-light: inset 0 1px 0 0 rgba(255,255,255,.06);
  box-shadow: var(--shadow-elevation), var(--rim-light);
  ```
  Mit `light-dark()` kombinierbar, um den Rand im Hellmodus wegzulassen:
  `inset 0 0 0 1px light-dark(transparent, oklch(1 0 0 / 8%))`. Mehrere
  aktuelle Guides (CodeFronts, design.dev) beschreiben dieselbe Grundidee
  unter Namen wie „Masked Border Glow" — ein Rand, der nur im Dunkeln
  sichtbar wird, wirkt wie Streiflicht statt wie eine gezeichnete Linie.
- **Neumorphismus/Soft-UI (doppelter Light/Dark-Schatten, geprägter Look)**
  passt zur „weichen Materialität", ist aber laut mehreren Quellen (u. a.
  setproduct.com, euleinstitute.com) *nicht* für eine ganze Oberfläche
  geeignet — der geringe Kontrast zwischen Element und Hintergrund macht ihn
  zum Accessibility-Risiko bei Fließtext und dichten Inhaltslisten. Für eine
  Chat-Historie mit dichtem Text ist das ein reales Problem: Chappes
  Nachrichten-Bubbles sind das Hauptinhaltselement, nicht ein einzelner
  Toggle-Button. Empfehlung: Neumorphismus-Technik (doppelter Schatten, weich)
  gezielt für einzelne Bedienelemente (Suchfeld, Statistik-`<details>`-Toggle)
  einsetzen, nicht für die Bubbles selbst — dort bleibt Textkontrast
  (WCAG-Kontrastverhältnis Text/Bubble-Hintergrund) das härtere Kriterium.

**Was davon trägt in reinem CSS, ohne JS:** alles Genannte. Schattenschichtung
ist ein mehrwertiger `box-shadow`, Oberflächenaufhellung ist
`color-mix(in oklch, …)` oder vorab in `tokens.json` festgelegte Hex-Stufen,
Randlicht ist ein `inset`-Schatten, `light-dark()` ist eine CSS-Funktion.
Nichts davon braucht JavaScript — passt zum bestehenden Muster in
`render/html.py`, wo `_JS` ausschließlich für Suche und Lightbox zuständig
ist, nicht für Optik.

## Fallstricke

- **Drift zwischen generierter und gelesener CSS-Datei.** Wenn
  `src/chappe/render/_tokens.css` von Hand aus dem Generator-Lauf committet
  wird (nötig, weil zur Laufzeit kein Node/Python-Regenerieren stattfinden
  soll), kann sie veralten, wenn jemand `design/tokens.json` ändert und den
  Generator vergisst. Absicherung: ein Test in `tests/`, der
  `build_css(design/tokens.json)` neu berechnet und mit dem committeten
  `_tokens.css` vergleicht — passt zur bestehenden Testkultur des Projekts
  (`tests/test_render.py` hält bereits andere Invarianten fest).
- **Style Dictionarys `css/variables`-Format liefert kein Light/Dark von
  sich aus.** Falls das Team Style Dictionary doch einsetzt: Es braucht ein
  eigenes registriertes Format (`registerFormat`) oder die
  `$mods`-Konvention — das Standardformat erzeugt nur einen Modus pro Lauf
  ([alwaystwisted.com, Teil 7](https://www.alwaystwisted.com/articles/a-design-tokens-workflow-part-7)).
  Nicht „einfach installieren und es funktioniert".
- **DTCG 2025.10 ist frisch.** Erste stabile Version seit Oktober 2025,
  Tool-Support (gerade das Resolver Module für Theming) ist noch im
  Nachziehen — Terrazzo 2.0 mit vollem Resolver-Support war zum
  Recherchezeitpunkt noch nicht erschienen. Bei Bindung an ein Tool: Version
  pinnen, nicht „latest" in CI.
- **`light-dark()` schaltet nicht ohne `color-scheme: light dark`.** Leicht
  vergessen, dann bleibt die Seite in einem Modus hängen, ohne
  Fehlermeldung.
- **`box-shadow` auf dunklem Grund ist meist unsichtbar**, nicht nur
  „schwächer" — das ist keine Geschmacksfrage, sondern Physik von Kontrast.
  Wer Schattenwerte 1:1 vom Hell- ins Dunkelmodus kopiert (was
  `render/html.py` mit dem heutigen `--shadow`-Token effektiv tut — nur die
  Opacity steigt von `.08` auf `.4`), verschenkt Tiefenwirkung. Ein
  Oberflächenaufhellungs-Token ist der wirksamere Hebel als ein stärkerer
  Schatten.
- **Neumorphismus als Vollflächenstil** ist ein wiederkehrend genannter
  Accessibility-Fallstrick (Kontrast) — siehe Ästhetik-Abschnitt oben, gilt
  hier doppelt, weil Chappes Hauptinhalt Lesetext ist.
- **Package-Data-Deklaration vergessen.** `_tokens.css` muss explizit als
  Package-Data in `pyproject.toml` eingetragen sein, sonst fehlt sie im
  gebauten Wheel, obwohl sie lokal im Checkout existiert — Tests, die lokal
  grün sind, verschleiern das; ein Test sollte gegen das gebaute
  Distributionsartefakt prüfen, nicht nur gegen den Checkout.

## Quellen

- [Design Tokens Community Group — Technical Reports 2025.10](https://www.designtokens.org/tr/2025.10/)
- [Design Tokens Format Module 2025.10 (Draft-Referenz)](https://www.designtokens.org/tr/drafts/format/)
- [Design Tokens Resolver Module 2025.10 (Draft-Referenz)](https://www.designtokens.org/tr/drafts/resolver/)
- [Style Dictionary — offizielle Seite](https://styledictionary.com/)
- [Style Dictionary v4 Migration Guide (Node ≥ 18)](https://styledictionary.com/versions/v4/migration/)
- [Always Twisted — „A Design Tokens Workflow", Teil 7: Light/Dark mit Style Dictionary](https://www.alwaystwisted.com/articles/a-design-tokens-workflow-part-7)
- [Terrazzo — Dokumentation](https://terrazzo.app/docs/)
- [Terrazzo — Projektseite](https://terrazzo.app/)
- [Atlassian Design System — Elevation](https://atlassian.design/foundations/elevation)
- [Material Design 3 — Elevation](https://m3.material.io/styles/elevation)
- [uxcel.com — Mastering Elevation for Dark UI](https://uxcel.com/blog/mastering-elevation-for-dark-ui-a-comprehensive-guide-342)
- [Josh W. Comeau — Designing Beautiful Shadows in CSS](https://www.joshwcomeau.com/css/designing-shadows/)
- [Josh W. Comeau — Shadow Palette Generator](https://www.joshwcomeau.com/shadow-palette/)
- [MDN — `light-dark()` CSS-Funktion](https://developer.mozilla.org/en-US/docs/Web/CSS/color_value/light-dark)
- [caniuse — `light-dark()` Support-Tabelle](https://caniuse.com/mdn-css_types_color_light-dark)
- [Evil Martians — OKLCH in CSS: why we moved from RGB and HSL](https://evilmartians.com/chronicles/oklch-in-css-why-quit-rgb-hsl)
- [Modern CSS Tools — Modern CSS Color: oklch, color-mix()](https://moderncsstools.com/guides/modern-colors/)
- [Scientific Python Development Guide — Including data files](https://learn.scientific-python.org/development/patterns/data-files/)
- [setuptools — Data Files Support](https://setuptools.pypa.io/en/latest/userguide/datafiles.html)

## Offene Punkte

- Die genaue Material-Design-Tabelle für tonale Elevation (Prozentsatz
  Weiß-Overlay je dp-Stufe, oft als 5/7/8/9/11/12/14/15/16 % kolportiert)
  konnte ich in dieser Recherche **nicht primärquellen-sicher belegen** —
  `m3.material.io/styles/elevation` ist eine JS-gerenderte Seite, aus der der
  Fetch keinen Fließtext extrahieren konnte, und die MDC-README auf GitHub
  enthält keine Tabelle. Vor Übernahme konkreter Prozentwerte in
  `tokens.json`: Seite im Browser öffnen und die Tabelle visuell prüfen,
  nicht aus Sekundärquellen zitieren.
- Die tatsächliche Verzeichnisstruktur des künftigen Vue/Electron-Frontends
  existiert noch nicht im Repo — `frontend/src/styles/tokens.css` in diesem
  Dokument ist ein Vorschlag, kein bestätigter Pfad. Sobald das
  Frontend-Grundgerüst steht, muss der Zielpfad im Generator entsprechend
  angepasst werden.
- Welche Chromium-Version das Electron-Frontend mitbringt (relevant für
  verlässlichen `light-dark()`-Support dort) habe ich nicht geprüft — sofern
  eine aktuelle Electron-Version zum Einsatz kommt, ist das laut MDN
  unkritisch, aber unverifiziert für diesen konkreten Fall.
- Die genaue aktuelle Versionsnummer von Style Dictionary (Patch-Stand,
  2026) ließ sich aus der Startseite nicht extrahieren — falls das Tool doch
  gewählt wird, vor Konfiguration den aktuellen Stand auf npm prüfen.
- Kein Python-Paket für DTCG-Parsing gefunden — falls sich die Anforderungen
  später Richtung Vollspezifikation (Resolver-Module, Farbraum-Interpolation)
  entwickeln, bleibt unklar, ob Eigenbau dann noch verhältnismäßig ist oder
  ob sich der Wechsel zu einem Node-Tool trotz der Projektphilosophie lohnt.
