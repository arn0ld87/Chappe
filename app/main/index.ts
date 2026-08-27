/**
 * Hauptprozess: startet das Sidecar-Binary, öffnet das Fenster gehärtet,
 * verdrahtet IPC und kümmert sich um sauberes Beenden.
 */

import { BrowserWindow, app, session } from "electron";
import path from "node:path";
import { SidecarClient } from "./sidecar";
import { resolveDbPath } from "./paths";
import { registerRpcHandlers } from "./ipc";

// Bestimmt u. a. app.getPath("userData") — das plattformübliche
// App-Verzeichnis aus docs/gui-plan.md ("~/Library/Application Support/Chappe",
// "%APPDATA%\Chappe", …). Muss vor jedem getPath()-Aufruf gesetzt sein.
app.setName("Chappe");

const sidecar = new SidecarClient();
let mainWindow: BrowserWindow | null = null;
let quitting = false;

function createWindow(): BrowserWindow {
  const window = new BrowserWindow({
    width: 1000,
    height: 700,
    webPreferences: {
      // Härtung ab der ersten Zeile — trotz Electron-Defaults explizit
      // gesetzt, als dokumentierte Absicht statt stillschweigend geerbtem
      // Verhalten, das ein späterer Refactor kippen könnte (siehe
      // docs/research/haertung.md, Abschnitt 1, und docs/gui-plan.md,
      // Abschnitt "Nicht verhandelbar").
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      webSecurity: true,
      preload: path.join(__dirname, "../preload/index.js"),
    },
  });

  // Öffnet keine neuen Fenster/Tabs (Security-Checkliste Punkt 14) und folgt
  // keiner Navigation weg von der eigenen, gebündelten Oberfläche
  // (Punkt 13) — Chappe lädt nie etwas von aussen.
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event, url) => {
    if (url !== window.webContents.getURL()) {
      event.preventDefault();
    }
  });

  return window;
}

/**
 * Setzt die Content-Security-Policy für jede Antwort in der Session — das
 * funktioniert unabhängig davon, ob per loadURL (Entwicklung, Vite-Dev-
 * Server) oder loadFile (gepackter Zustand) geladen wird, und braucht daher
 * kein <meta http-equiv="Content-Security-Policy">-Tag im Renderer-HTML
 * (siehe docs/research/haertung.md, Abschnitt 3).
 *
 * style-src erlaubt 'unsafe-inline' ausschliesslich im Entwicklungsmodus:
 * Vites Dev-Server injiziert HMR- und Fehler-Overlay-Styles über
 * script-erzeugte <style>-Elemente, die eine strikte style-src ohne
 * Ausnahme blockieren würde. Der gepackte Build enthält in Slice 0 ohnehin
 * kein eigenes CSS (bewusst unstyled, siehe Auftrag) und braucht diese
 * Ausnahme nicht. connect-src erlaubt aus demselben Grund im Entwicklungs-
 * modus zusätzlich den lokalen WebSocket, über den Vite HMR-Updates schickt.
 */
function applyContentSecurityPolicy(): void {
  const isDev = !app.isPackaged;
  const styleSrc = isDev ? "style-src 'self' 'unsafe-inline'" : "style-src 'self'";
  const connectSrc = isDev ? "connect-src 'self' ws://localhost:*" : "connect-src 'self'";

  const csp = [
    "default-src 'none'",
    "script-src 'self'",
    styleSrc,
    "img-src 'self'",
    connectSrc,
    "font-src 'self'",
  ].join("; ");

  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        "Content-Security-Policy": [csp],
      },
    });
  });
}

app.whenReady().then(async () => {
  applyContentSecurityPolicy();

  const dbPath = resolveDbPath();
  try {
    await sidecar.start(dbPath);
  } catch (error) {
    // Kein Absturz der App, wenn der Sidecar (noch) nicht startet — das
    // Fenster zeigt dann einen Fehler statt einer Chatliste (siehe
    // app/renderer/src/App.vue). Für Slice 0 insbesondere relevant, solange
    // `chappe rpc` als Subkommando noch nicht existiert.
    console.error("[main] Sidecar konnte nicht gestartet werden:", error);
  }

  mainWindow = createWindow();
  registerRpcHandlers(sidecar, () => mainWindow);

  // electron-vite setzt ELECTRON_RENDERER_URL im Entwicklungsmodus auf die
  // Adresse des Vite-Dev-Servers; im gepackten Zustand ist die Variable
  // nicht gesetzt, und die gebaute index.html wird per loadFile geladen.
  const devServerUrl = process.env.ELECTRON_RENDERER_URL;
  if (devServerUrl) {
    await mainWindow.loadURL(devServerUrl);
  } else {
    await mainWindow.loadFile(path.join(__dirname, "../renderer/index.html"));
  }

  mainWindow.webContents.on("render-process-gone", (_event, details) => {
    // Der Sidecar hängt am Hauptprozess, nicht am Renderer — sein Beenden
    // über before-quit (unten) ist dadurch unabhängig von einem
    // Renderer-Absturz sichergestellt; genau das verlangt die
    // Slice-0-Verifikation ("auch nach einem Absturz des Renderers").
    // Ohne Renderer gibt es in diesem Durchstich keine Oberfläche mehr, die
    // sich sinnvoll anzeigen liesse — ein Neuladen/Wiederherstellen ist
    // erst für spätere Slices vorgesehen — deshalb fahren wir sauber
    // herunter, statt ein leeres Fenster offen zu lassen.
    console.error("[main] Renderer-Prozess abgestürzt:", details.reason);
    app.quit();
  });
});

app.on("before-quit", (event) => {
  // Zweistufiges Beenden hier und ausdrücklich NICHT in window-all-closed:
  // Auf macOS bleibt die App nach dem Schliessen aller Fenster im Dock
  // aktiv, und window-all-closed feuert dort beim eigentlichen Quit gar
  // nicht (siehe docs/research/sidecar.md, Abschnitt 2a). before-quit
  // läuft dagegen bei Cmd+Q oder app.quit() zuverlässig vor dem
  // Fensterabbau.
  if (quitting) return; // zweiter Durchlauf durch das app.quit() unten
  quitting = true;
  event.preventDefault();
  void sidecar.shutdown().finally(() => app.quit());
});

app.on("window-all-closed", () => {
  // Löst hier bewusst KEINE Sidecar-Logik aus — das passiert ausschliesslich
  // in before-quit. app.quit() sorgt nur dafür, dass before-quit auf
  // Windows/Linux überhaupt feuert, wenn das letzte Fenster schliesst.
  if (process.platform !== "darwin") {
    app.quit();
  }
});
