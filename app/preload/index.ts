/**
 * Preload-Skript: contextBridge, minimal und explizit. Genau zwei
 * Funktionen nach aussen — kein generisches Durchreichen von
 * ipcRenderer.send/invoke (siehe docs/research/haertung.md, Abschnitt 2,
 * das dortige "unsicher"-Beispiel, das genau danach aussieht, was man
 * versehentlich schreibt, wenn man "schnell mal was durchreichen" will).
 *
 * Läuft mit sandbox: true — hier steht kein volles Node-Environment zur
 * Verfügung, nur der von Electron gepolyfillte require() für einen
 * begrenzten Modul-Satz (u. a. "electron" selbst).
 */

import { contextBridge, ipcRenderer } from "electron";
import type { ChappeApi, ChatSummary, PingResult } from "../shared/protocol";

const chappeAPI: ChappeApi = {
  ping: () => ipcRenderer.invoke("rpc:ping") as Promise<PingResult>,
  listChats: () => ipcRenderer.invoke("rpc:list_chats") as Promise<ChatSummary[]>,
};

contextBridge.exposeInMainWorld("chappeAPI", chappeAPI);
