/**
 * Pfadauflösung für den Sidecar-Prozess: wo liegt das `chappe`-Binary
 * (bzw. das lokale Python-Paket in der Entwicklung), und welche Datenbank
 * soll es öffnen.
 */

import { app } from "electron";
import path from "node:path";

/**
 * Löst den Pfad zur SQLite-Datenbank auf, die der Sidecar öffnen soll.
 *
 * Reihenfolge (erster Treffer gewinnt):
 *   1. `CHAPPE_DB_PATH` — Umgebungsvariable für manuelles Testen ohne
 *      Onboarding-Oberfläche (die kommt erst in Slice 4).
 *   2. `<userData>/chappe.db` — das plattformübliche App-Verzeichnis aus
 *      docs/gui-plan.md, Abschnitt "Struktur" ("Ablage von Datenbank und
 *      Medien im plattformüblichen App-Verzeichnis").
 *
 * WICHTIG: Kein echter Backup-Pfad darf hier je fest verdrahtet werden — die
 * beiden lokalen Test-Backups leben laut CLAUDE.md ("Die beiden echten
 * Backups") absichtlich außerhalb dieses Repos, in `~/.config/chappe/sources.json`.
 * Für einen manuellen Testlauf von Slice 0 wird der Pfad über `CHAPPE_DB_PATH`
 * von außen hereingereicht, nicht im Code hinterlegt.
 */
export function resolveDbPath(): string {
  const override = process.env.CHAPPE_DB_PATH;
  if (override && override.trim() !== "") {
    return override;
  }
  return path.join(app.getPath("userData"), "chappe.db");
}

export interface SidecarCommand {
  command: string;
  args: string[];
  env: NodeJS.ProcessEnv;
}

/**
 * Löst Kommando, Argumente und Umgebung für den Sidecar-Start auf.
 *
 * Entwicklungsmodus (`app.isPackaged === false`): startet das lokale
 * Python-Paket über `python3 -m chappe rpc`, mit `PYTHONPATH=src` — kein
 * PyInstaller-Build nötig für lokale Entwicklung.
 *
 * Gepackter Zustand: startet das PyInstaller-Binary unter
 * `process.resourcesPath/sidecar/` — dorthin kopiert electron-builder es
 * später über `extraResources` (siehe docs/research/sidecar.md, Abschnitt 1;
 * dieser Teil des Durchstichs bereitet nur die Pfadauflösung vor, der
 * eigentliche electron-builder-/PyInstaller-Build folgt in Slice 1).
 */
export function resolveSidecarCommand(dbPath: string): SidecarCommand {
  if (app.isPackaged) {
    // Name muss zu BINARY_NAME in packaging/chappe.spec passen — der Build
    // erzeugt "chappe-rpc" (bzw. "chappe-rpc.exe"), nicht "chappe".
    const binaryName = process.platform === "win32" ? "chappe-rpc.exe" : "chappe-rpc";
    const binaryPath = path.join(process.resourcesPath, "sidecar", binaryName);
    return {
      command: binaryPath,
      args: ["rpc", "--db", dbPath],
      env: { ...process.env },
    };
  }

  // __dirname zeigt zur Laufzeit auf dist/main/ (siehe app/electron.vite.config.ts,
  // main.build.outDir) — zwei Ebenen höher liegt die Projektwurzel, in der
  // src/ (das Python-Paket) und package.json liegen.
  const repoRoot = path.resolve(__dirname, "..", "..");
  const pythonExecutable =
    process.env.CHAPPE_PYTHON ?? (process.platform === "win32" ? "python" : "python3");

  return {
    command: pythonExecutable,
    args: ["-m", "chappe", "rpc", "--db", dbPath],
    env: {
      ...process.env,
      PYTHONPATH: path.join(repoRoot, "src"),
      // Python puffert stdout blockweise, sobald es kein TTY ist — ohne das
      // hier bekäme der readline-Listener auf Node-Seite minutenlang nichts,
      // obwohl Python längst geantwortet hat (docs/research/sidecar.md,
      // Fallstricke).
      PYTHONUNBUFFERED: "1",
    },
  };
}
