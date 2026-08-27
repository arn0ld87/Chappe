import { resolve } from "node:path";
import { defineConfig, externalizeDepsPlugin } from "electron-vite";
import vue from "@vitejs/plugin-vue";

/**
 * electron-vite baut drei getrennte Bündel aus einer Konfiguration: den
 * Hauptprozess, das Preload-Skript und den Renderer.
 *
 * Diese Datei liegt bewusst unter app/, nicht im Repo-Wurzelverzeichnis —
 * der Slice-0-Auftrag erlaubt Änderungen nur unter app/**, package.json und
 * .gitignore. package.json ruft sie deshalb explizit über
 * `electron-vite dev/build --config app/electron.vite.config.ts` auf.
 *
 * Trotzdem werden alle Pfade hier relativ zum Arbeitsverzeichnis aufgelöst,
 * in dem npm die Skripte startet — das ist die Projektwurzel (dort liegt
 * package.json), nicht der Ordner dieser Datei. `resolve()` ohne Basis
 * verwendet process.cwd(), nicht den Ort der Konfigurationsdatei — das ist
 * dieselbe Konvention, die die offizielle electron-vite-Beispielkonfiguration
 * verwendet.
 *
 * Alle drei Prozesse bauen nach dist/<prozess>/ im Repo-Wurzelverzeichnis.
 * app/main/paths.ts nimmt genau dieses Layout für die
 * Entwicklungs-Pfadauflösung an (zwei Ebenen über dist/main/ liegt die
 * Projektwurzel mit src/ und package.json).
 */
export default defineConfig({
  main: {
    // externalizeDepsPlugin: Node-Module aus node_modules werden nicht
    // gebündelt, sondern zur Laufzeit über require() geladen — die von
    // electron-vite empfohlene Voreinstellung für den Hauptprozess.
    plugins: [externalizeDepsPlugin()],
    build: {
      outDir: "dist/main",
      rollupOptions: {
        input: resolve("app/main/index.ts"),
      },
    },
  },
  preload: {
    plugins: [externalizeDepsPlugin()],
    build: {
      outDir: "dist/preload",
      rollupOptions: {
        input: resolve("app/preload/index.ts"),
      },
    },
  },
  renderer: {
    root: resolve("app/renderer"),
    plugins: [vue()],
    build: {
      outDir: resolve("dist/renderer"),
      rollupOptions: {
        input: resolve("app/renderer/index.html"),
      },
    },
  },
});
