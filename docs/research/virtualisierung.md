# Virtualisiertes Scrollen mit variablen Elementhöhen in Vue 3

Recherchestand: 2026-08-26. Aufgabe: Chatverlauf über bis zu 39.000 Nachrichten in der
kommenden Electron-Desktop-App von Chappe, mit datenabhängigem Abstand zwischen
Nachrichten (abhängig von der verstrichenen Zeit), erst zur Laufzeit final bekannter
Elementhöhe, einem Zeitstrahl am Rand und Sprung an ein Datum.

## Abriss

Für Vue 3 gibt es drei ernstzunehmende Virtualisierungsbibliotheken plus die
Eigenbau-Option:

| Bibliothek | Version (npm, geprüft 2026-08-26) | Vue-Peer | Ansatz |
|---|---|---|---|
| **TanStack Virtual** (`@tanstack/vue-virtual`) | 3.13.36 | `^2.7.0 \|\| ^3.0.0` | headless, Composition-API (`useVirtualizer`) |
| **vue-virtual-scroller** (Akryum) | 3.0.5 | `^3.3.0` | fertige Komponenten (`DynamicScroller`) |
| **virtua** (inokawa) | 0.50.5 | `>=3.2` (optional) | fertige Komponente (`VList`), Multi-Framework |

Alle drei lösen dieselben drei Grundprobleme unterschiedlich explizit: (1) Höhe erst
nach dem Rendern messen, (2) beim Voranstellen älterer Elemente die Scrollposition
nicht springen lassen, (3) programmatisch zu einem Index springen, obwohl die
Positionen vorher nur geschätzt sind. Ein Zeitstrahl-Scrubber mit Datumssprung ist in
keiner der drei Bibliotheken ein fertiges Feature — das bleibt in jedem Fall
App-eigener Code auf Basis von `scrollToIndex`/`scrollToOffset`.

## Empfehlung

**virtua (`virtua/vue`, Komponente `VList`)** als primäre Wahl.

Begründung:

- Die Prop **`shift`** deckt das „ältere Nachrichten nach oben nachladen"-Muster direkt
  ab: *„Maintains scroll position from the end when items are added to the start"*
  ([API/DeepWiki](https://deepwiki.com/inokawa/virtua/4.2-vlist-component)). Intern
  verfolgt der `VirtualStore` `jump`/`pendingJump`-Werte, berechnet Größendifferenzen
  oberhalb der aktuellen Scrollposition und kompensiert sie über `$fixScrollJump()`,
  inklusive bewusster Verzögerung während iOS-Momentum-Scroll oder aktivem
  Smooth-Scroll ([Scroller-Internals/DeepWiki](https://deepwiki.com/inokawa/virtua/3.3-scroller-and-scroll-management)).
  Bei TanStack Virtual muss man das äquivalente Verhalten (`anchorTo`, `getItemKey`,
  `followOnAppend`) selbst korrekt verdrahten — machbar, aber mehr eigene
  Verantwortung.
- **Realer Produktionsnachweis für exakt dieses Szenario:** Rocket.Chat hat seine
  Hauptnachrichtenliste per PR auf virtuas `VList` migriert, explizit um die
  Performance bei einer chatartigen, hochvolumigen Liste zu verbessern
  ([PR #40105](https://github.com/RocketChat/Rocket.Chat/pull/40105)). Konkrete
  Vorher/Nachher-Zahlen enthält die PR-Beschreibung nicht, aber die
  Technologieentscheidung selbst ist ein belastbares Signal — Rocket.Chat ist kein
  Nischenprojekt.
- Kleinster Fußabdruck (~3 kB gzip je Framework-Adapter,
  [GitHub-Readme](https://github.com/inokawa/virtua/blob/main/README.md)) — für die
  Bundle-Größe der Electron-App nicht kritisch, aber ein Indiz für schlanke,
  fokussierte Implementierung ohne versteckte Komplexität.
- `cache`-Prop (Typ `CacheSnapshot`) erlaubt, bereits gemessene Größen und die
  Scrollposition zu sichern/wiederherzustellen — nützlich, um beim Zeitstrahl-Sprung
  über bereits besuchte Bereiche keine Höhen neu zu erraten.

**Zweite Wahl: TanStack Virtual**, wenn die feingranulare Kontrolle über
`anchorTo`/`followOnAppend`/`rangeExtractor` gebraucht wird — `rangeExtractor` erlaubt
z. B. das Einschleusen zusätzlicher, nicht-virtualisierter Items wie einen
Sticky-Datums-Header im Zeitstrahl
([Virtualizer-API](https://tanstack.com/virtual/latest/docs/api/virtualizer)).
TanStack pflegt außerdem einen **dedizierten Chat-Leitfaden**
([tanstack.com/virtual/latest/docs/chat](https://tanstack.com/virtual/latest/docs/chat))
mit `anchorTo: 'end'`, `followOnAppend`, `scrollEndThreshold`, `scrollToEnd()`,
`isAtEnd()`, `getDistanceFromEnd()` — explizit für genau Chappes Anwendungsfall
geschrieben. Chappes Stack (Vue 3 + Vite, Astro) hat aktuell keine TanStack-Abhängigkeit,
daher entfällt der sonst naheliegende „ohnehin schon im Projekt"-Grund.

**Nicht empfohlen: vue-virtual-scroller.** Der `DynamicScroller` „entdeckt" Höhen
progressiv beim Rendern über einen `ResizeObserver` auf dem gesamten sichtbaren
Bereich. [Issue #130](https://github.com/Akryum/vue-virtual-scroller/issues/130)
(offen seit 2019) beschreibt, dass im `pageMode` **jede** Höhenänderung eines
beliebigen Elements — etwa ein nachladendes Bild — `forceUpdate(false)` auslöst, was
**alle** gespeicherten Höhen verwirft, nicht nur die des geänderten Elements. Das ist
exakt Chappes Kernrisiko: Im kleinen Testbackup liegen 548 von 1.050 Anhängen nicht
lokal vor (`local_path IS NULL` ist Normalfall, siehe Projekt-CLAUDE.md) und müssen
beim ersten Rendern nachgeladen werden, jeder davon ein potenzieller
Höhenänderungs-Trigger.

**Nicht empfohlen: Eigenbau von Grund auf.** Alle drei Bibliotheken lösen dieselben
Probleme (Jump-Kompensation, Messung, Reconciliation beim Sprung), die man sonst
nachbauen müsste. Die TanStack-Diskussion zu umgekehrten Listen zeigt konkret, wie viele
Randfälle dabei übersehen werden (siehe Fallstricke).

## Fallstricke

- **Index-Keys brechen bei jedem Prepend.** Nach dem Nachladen älterer Nachrichten
  verschieben sich alle folgenden Indizes; jede der drei Bibliotheken braucht einen
  stabilen Schlüssel (Nachrichten-`id`, nicht Array-Index) — dokumentiert explizit im
  TanStack-Chat-Leitfaden (`getItemKey: (index) => messages[index]!.id`) und implizit
  bei virtua, wo Größen „per key" gespeichert werden
  ([DeepWiki VList](https://deepwiki.com/inokawa/virtua/4.2-vlist-component)).
- **`estimateSize`/`itemSize` grob raten kostet Genauigkeit, die man sich bei Chappe
  schenken kann.** Bei Chappe zerfällt die Elementhöhe in einen Abstands-Anteil
  (abhängig von Δt zwischen Nachrichten — bereits aus `sent_at` in der SQLite-DB
  vorab exakt berechenbar) und einen Inhalts-Anteil (Textumbruch, Bildhöhe — erst zur
  Laufzeit bekannt). Eine `estimateSize`-Funktion, die den Abstands-Anteil exakt statt
  geschätzt liefert, verkleinert den Fehler, den jede Bibliothek sonst per
  Reconciliation-Schleife ausgleichen muss. Dies ist eine eigene Schlussfolgerung aus
  den recherchierten `estimateSize`/`measureElement`-Mechanismen, nicht durch eine
  einzelne Quelle belegt.
- **Bilder ohne reservierten Platz verursachen einen zweiten Sprung.** Ohne feste
  Höhe/`aspect-ratio` schätzt jede Bibliothek zunächst 0 oder einen Platzhalterwert;
  nach dem Laden triggert der `ResizeObserver` eine Nachmessung, die bei TanStack via
  `measureElement`/`anchorTo` sauber kompensiert wird, bei vue-virtual-scroller aber im
  `pageMode` laut Issue #130 zum Totalverlust aller Höhen führen kann.
- **`scrollToIndex` trifft bei Tausenden Items mit dynamischer Größe nicht immer exakt.**
  [TanStack-Issue #216](https://github.com/TanStack/virtual/issues/216) beschreibt bei
  ~10.000 Items ein `scrollToIndex`, das mal exakt ans Ende springt, mal bei Zeile
  9993 stehen bleibt — inhärent, weil die wahre Position erst nach dem tatsächlichen
  Messen aller dazwischenliegenden Items feststeht. Für den Zeitstrahl-Sprung in Chappe
  (bis zu 39.000 Items) heißt das: einen einzelnen `scrollToIndex`-Aufruf nicht als
  verlässlich betrachten, sondern nach dem nächsten Render erneut prüfen/korrigieren
  (Reconciliation). TanStacks eigene Doku beschreibt für den Smooth-Scroll-Beispielcode
  einen solchen Reconciliation-Mechanismus, der exakte Quellcode war unter der
  recherchierten v3-Beispiel-URL aber nicht mehr abrufbar (404) — vor Implementierung
  in der aktuellen Doku erneut suchen.
- **Rückwärts-Scrollen mit dynamischen Höhen ruckelt/springt** — mehrfach in
  TanStacks Issue-Tracker dokumentiert
  ([#524](https://github.com/TanStack/virtual/issues/524),
  [#659](https://github.com/TanStack/virtual/issues/659), workaround dort: beim
  Rückwärts-Scrollen gecachte statt neu gemessene Werte verwenden). Der aktuelle
  Perf-Release adressiert das laut Blogpost über „direktionales Gating" (Vorwärts-Scroll
  behält Positionsanpassungen, Rückwärts-Scroll überspringt sie standardmäßig) — sollte
  vor Einsatz gegen die tatsächlich installierte Version geprüft werden, statt sich auf
  die Blog-Aussage allein zu verlassen.
- **CSS `overflow-anchor` hilft bei virtualisierten Listen nicht.** In Chappes
  Electron-App (Chromium) wäre `overflow-anchor` technisch verfügbar (unterstützt seit
  Chrome 56; Safari unterstützt es bis heute nicht, laut
  [caniuse](https://caniuse.com/css-overflow-anchor) Stand 2026 nur in der Safari
  Technology Preview — für Chappe irrelevant, da kein Safari-Ziel). MDN weist aber
  explizit darauf hin, dass der Anchor-Knoten, den der Browser zur Stabilisierung
  wählt, in virtualisierten Listen genau der Knoten ist, den die Virtualisierung aus
  dem DOM entfernt — die native Ankerung geht damit ins Leere. Empfehlung von MDN:
  `overflow-anchor: none` auf dem virtualisierten Container setzen und stattdessen der
  Bibliotheks-eigenen JS-Ankerung vertrauen
  ([MDN: Scroll anchoring](https://developer.mozilla.org/en-US/docs/Web/CSS/CSS_scroll_anchoring/Scroll_anchoring)).
- **Reverse-Layout-Hacks sind ein Holzweg.** `flex-direction: column-reverse` oder
  `transform: scaleY(-1)`, um eine Chat-Liste „von unten" zu virtualisieren, brechen
  laut [TanStack-Diskussion #195](https://github.com/TanStack/virtual/discussions/195)
  bei asynchronen Bildhöhenänderungen, bei gleichzeitigem Prepend+Append, bei
  Tab-Reihenfolge/Keyboard-Navigation und beim Sticky-Bottom-Autoscroll. Die Diskussion
  selbst mündet in der Empfehlung, auf eine Bibliothek zu wechseln statt das selbst zu
  bauen.
- **vue-virtual-scroller ist ESM-only in der Vue-3-Linie** (`vue-virtual-scroller`
  README, siehe Quellen) — mit Vite/Electron unproblematisch, aber bei älteren
  CommonJS-Toolchains relevant.

## Quellen

- TanStack Virtual — Virtualizer-API (`estimateSize`, `measureElement`, `overscan`,
  `scrollToIndex`, `scrollToOffset`, `getVirtualItems`, `getTotalSize`, `onChange`,
  `rangeExtractor`): https://tanstack.com/virtual/latest/docs/api/virtualizer
- TanStack Virtual — Chat-Leitfaden (`anchorTo`, `followOnAppend`,
  `scrollEndThreshold`, `getItemKey`, `scrollToEnd`, `isAtEnd`,
  `getDistanceFromEnd`): https://tanstack.com/virtual/latest/docs/chat
- TanStack Virtual — Blogpost „Chat UIs Are Lists Until They Aren't" (Design-Rationale,
  „this still isn't a chat component"): https://tanstack.com/blog/tanstack-virtual-chat
- TanStack Virtual — Blogpost zu Performance/iOS (Benchmarks Cold Mount 100k/500k,
  Resize-Storm 10k, direktionales Gating, iOS-Momentum-Fix):
  https://tanstack.com/blog/tanstack-virtual-perf-and-ios
- TanStack Virtual — Vue-spezifische Doku (`useVirtualizer`, `useWindowVirtualizer`
  Signaturen): https://tanstack.com/virtual/v3/docs/framework/vue/vue-virtual
- TanStack Virtual — Issue „dynamic size issue" (Rückwärts-Scroll):
  https://github.com/TanStack/virtual/issues/524
- TanStack Virtual — Issue „Scrolling up with dynamic heights stutters and jumps":
  https://github.com/TanStack/virtual/issues/659
- TanStack Virtual — Issue „scrollToIndex and dynamic size" (Ungenauigkeit bei ~10k
  Items): https://github.com/TanStack/virtual/issues/216
- TanStack Virtual — Diskussion „Any guidance on a reversed virtual list with dynamic
  elements?" (Reverse-Layout-Hacks, Empfehlung Richtung virtua):
  https://github.com/TanStack/virtual/discussions/195
- vue-virtual-scroller — GitHub-Repo (Übersicht, Vue-3.3+-Anforderung, ESM-only):
  https://github.com/Akryum/vue-virtual-scroller
- vue-virtual-scroller — Issue #130 „DynamicScroller loses saved heights when an
  element changes height": https://github.com/Akryum/vue-virtual-scroller/issues/130
- virtua — GitHub-Repo/README (Bundle-Größe, Framework-Support, Feature-Liste):
  https://github.com/inokawa/virtua/blob/main/README.md
- virtua — DeepWiki „VList Component" (Props inkl. `shift`, `cache`, `bufferSize`,
  `keepMounted`, Handle-Methoden `scrollToIndex`/`scrollTo`/`scrollBy`):
  https://deepwiki.com/inokawa/virtua/4.2-vlist-component
- virtua — DeepWiki „Scroller and Scroll Management" (`jump`/`pendingJump`,
  `$fixScrollJump()`, iOS-Momentum-Verzögerung):
  https://deepwiki.com/inokawa/virtua/3.3-scroller-and-scroll-management
- Rocket.Chat — PR #40105, Migration der Nachrichtenliste auf virtuas `VList`:
  https://github.com/RocketChat/Rocket.Chat/pull/40105
- MDN — Overview of scroll anchoring (`overflow-anchor`, Wirkungslosigkeit bei
  virtualisierten Listen, Empfehlung `overflow-anchor: none`):
  https://developer.mozilla.org/en-US/docs/Web/CSS/CSS_scroll_anchoring/Scroll_anchoring
- caniuse — CSS overflow-anchor (Browser-Support-Matrix, Safari-Stand 2026):
  https://caniuse.com/css-overflow-anchor
- npm-Registry — aktuelle Versionsnummern und Peer-Dependencies (Stand 2026-08-26):
  https://registry.npmjs.org/@tanstack/vue-virtual/latest,
  https://registry.npmjs.org/vue-virtual-scroller/latest,
  https://registry.npmjs.org/virtua/latest
- shadcn-vue — `MessageScroller`-Komponente (kein Virtualisierer, aber dokumentiertes
  Props-Vokabular für Prepend-Stabilität: `preserveScrollOnPrepend`, `scrollToMessage`,
  `defaultScrollPosition="last-anchor"`) als Referenz für App-seitiges
  Scroll-Vokabular unabhängig von der gewählten Virtualisierungsbibliothek:
  https://www.shadcn-vue.com/docs/components/message-scroller

## Offene Punkte

- **vue-virtual-scroller-Props im Detail ungeklärt.** Die offizielle Doku
  (vue-virtual-scroller.netlify.app) ist clientseitig gerendert; per `WebFetch` kam
  nur das Navigationsgerüst ohne Inhalt zurück. Konkrete Prop-Namen wie
  `minItemSize`, `keyField`, `buffer` sowie die exakte `scrollToItem`-Signatur
  konnten deshalb nicht verifiziert werden. Vor einer Entscheidung für diese
  Bibliothek müsste die Doku im Browser oder der Quellcode direkt geprüft werden —
  ändert aber nichts an der Nichtempfehlung, die auf dem bestätigten Issue #130
  beruht.
- **Der TanStack-„Reconciliation"-Mechanismus für `scrollToIndex` bei dynamischer
  Größe ist nur indirekt belegt.** Die konkrete Beispielimplementierung unter der
  v3-Beispiel-URL (`.../vue/examples/smooth-scroll`) lieferte einen 404; die Aussage
  „nutzt einen Reconciliation-Mechanismus" stammt aus der Websuche-Zusammenfassung zu
  dieser Seite, nicht aus verifiziertem Quellcode. Vor Implementierung in der
  aktuellen TanStack-Doku (`tanstack.com/virtual/latest`) erneut nachschlagen.
- **Ob Signals Anhänge Breite/Höhe-Metadaten im `attachment.pointer` tragen**, mit
  denen sich Bildhöhen vor dem ersten Laden exakt vorausberechnen ließen (statt nur
  zu schätzen), wurde in dieser Recherche nicht geprüft — das wäre reiner
  Code-/Schema-Zugriff auf `chappe`, nicht Teil des recherchierten Web-Stands. Das
  wäre der größte Hebel gegen das „Bild ändert nach Laden die Höhe"-Problem und sollte
  vor der endgültigen Library-Entscheidung im Signal-Backup-JSON bzw. in `model.py`
  geprüft werden.
- **Keine unabhängigen Benchmarks gefunden**, die TanStack Virtual, vue-virtual-scroller
  und virtua unter identischen Bedingungen mit ~39.000 Items und echten variablen
  Höhen direkt vergleichen. Alle genannten Zahlen (Cold-Mount-Zeiten, Bundle-Größe,
  Vergleichstabellen) stammen aus den jeweils eigenen Blogs/READMEs der
  Projektbetreiber, nicht aus Drittquellen.
- **Zeitstrahl-Scrubber-UI ohne Referenzimplementierung.** Das in diesem Dokument
  skizzierte Muster (sortiertes Array + binäre Suche über `sent_at` + `scrollToIndex`)
  ist aus den Virtualizer-APIs abgeleitet, aber nicht an einem lauffähigen Beispiel
  verifiziert — insbesondere nicht, wie genau `getTotalSize()`/`scrollOffset` bei
  virtua (`scrollSize`/`scrollOffset`) mit stark ungleich verteilten Zeitabständen
  (z. B. Wochen ohne Nachrichten) in ein visuell sinnvolles Scrubber-Layout
  übersetzt werden sollten.
- Der Artikel „How to Keep Chat Scroll Stable While Loading Older Messages"
  (tech.ikas.com) tauchte in der Suche als einschlägig auf, war per `WebFetch` aber
  nicht abrufbar (HTTP 403). Das dort vermutlich beschriebene Muster wurde nicht
  direkt verifiziert, sondern nur aus TanStack-Doku, virtua-Internals und MDN
  rekonstruiert (siehe Fallstricke, Abschnitt zur manuellen Scroll-Korrektur).
