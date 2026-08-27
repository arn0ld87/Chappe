/**
 * Registriert die ipcMain-Handler für Slice 0 (ping, list_chats) und prüft
 * bei jedem Aufruf den Absender.
 */

import { type BrowserWindow, type IpcMainInvokeEvent, ipcMain } from "electron";
import type { SidecarClient } from "./sidecar";
import type { ChatSummary, PingResult } from "../shared/protocol";

/**
 * Prüft, ob eine IPC-Anfrage tatsächlich vom eigenen Hauptfenster stammt.
 *
 * Warum das nötig ist, obwohl contextIsolation/sandbox schon aktiv sind:
 * ipcMain.handle nimmt grundsätzlich von jedem Web-Frame Anfragen an, auch
 * von eingebetteten iframes oder — bei einem Electron-Framework-Bug wie
 * CVE-2022-23597/CVE-2022-29247 bei Element/Matrix — von Kind-Frames, die
 * eigentlich keinen ipcRenderer-Zugriff haben sollten (siehe
 * docs/research/haertung.md, Fallstrick 7). Die Sender-Prüfung fängt genau
 * diese Klasse Bug ab, unabhängig davon, ob sandbox/contextIsolation an sich
 * korrekt greifen.
 */
function isTrustedSender(
  event: IpcMainInvokeEvent,
  getWindow: () => BrowserWindow | null,
): boolean {
  const window = getWindow();
  if (!window || window.isDestroyed()) return false;
  return event.senderFrame?.url === window.webContents.getURL();
}

export function registerRpcHandlers(
  sidecar: SidecarClient,
  getWindow: () => BrowserWindow | null,
): void {
  ipcMain.handle("rpc:ping", async (event): Promise<PingResult> => {
    if (!isTrustedSender(event, getWindow)) {
      throw new Error("nicht autorisierter Absender");
    }
    return sidecar.call<PingResult>("ping");
  });

  ipcMain.handle("rpc:list_chats", async (event): Promise<ChatSummary[]> => {
    if (!isTrustedSender(event, getWindow)) {
      throw new Error("nicht autorisierter Absender");
    }
    return sidecar.call<ChatSummary[]>("list_chats");
  });
}
