/**
 * Typen für den NDJSON-Protokoll-Vertrag zwischen Electron und dem
 * `chappe rpc`-Sidecar-Prozess.
 *
 * Diese Datei bildet den Vertrag nur typisiert ab — sie definiert ihn nicht
 * neu. Quelle der Wahrheit ist der Protokoll-Vertrag aus dem Slice-0-Auftrag:
 *
 *   Anfrage:   {"id": <int>, "method": "<name>", "params": {...}}
 *   Erfolg:    {"id": <int>, "ok": true, "result": <beliebig>}
 *   Fehler:    {"id": <int>, "ok": false, "error": {"code": "<slug>", "message": "<deutscher Satz>"}}
 *   Ereignis:  {"event": "<name>", "data": {...}}     (unaufgefordert, ohne id)
 *
 * Wird sowohl vom Hauptprozess (app/main) als auch vom Preload-Skript
 * (app/preload) und vom Renderer (app/renderer) importiert — eine Quelle für
 * alle drei Prozesse.
 */

/** Eine Anfrage, die Electron über stdin an den Sidecar schickt. */
export interface RpcRequest {
  id: number;
  method: string;
  params?: Record<string, unknown>;
}

/** Erfolgsantwort des Sidecars. */
export interface RpcSuccessMessage<T = unknown> {
  id: number;
  ok: true;
  result: T;
}

/** Fehlerantwort des Sidecars. `id` ist `null`, wenn schon das Parsen der Anfrage scheiterte. */
export interface RpcErrorMessage {
  id: number | null;
  ok: false;
  error: { code: string; message: string };
}

/** Unaufgeforderte Nachricht des Sidecars, ohne zugehörige Anfrage. In Slice 0 ungenutzt. */
export interface RpcEventMessage {
  event: string;
  data: unknown;
}

/** Jede vollständige NDJSON-Zeile, die auf stdout des Sidecars ankommen kann. */
export type RpcIncomingMessage =
  | RpcSuccessMessage
  | RpcErrorMessage
  | RpcEventMessage;

/** Antwort der Methode "ping". */
export interface PingResult {
  version: string;
  protocol: number;
}

/**
 * Eine Zeile aus der View `v_chat_overview` (siehe src/chappe/schema.sql),
 * so wie sie der RPC-Adapter (src/chappe/rpc.py, von einem anderen Teil
 * dieses Durchstichs gebaut) voraussichtlich durchreicht. Slice 2 legt die
 * endgültige RPC-Form fest — bis dahin bleiben die Felder optional und ein
 * Fallback-Indexzugriff offen, damit der Renderer nicht bricht, falls sich
 * die genaue Form noch verschiebt.
 */
export interface ChatSummary {
  chat_id?: number;
  backup?: string;
  chat?: string;
  kind?: string;
  messages?: number;
  sent?: number;
  received?: number;
  attachments?: number;
  first_message?: string | null;
  last_message?: string | null;
  [key: string]: unknown;
}

/**
 * Die Brücke, die das Preload-Skript unter `window.chappeAPI` freigibt —
 * genau zwei Funktionen, nicht mehr, siehe app/preload/index.ts.
 */
export interface ChappeApi {
  ping: () => Promise<PingResult>;
  listChats: () => Promise<ChatSummary[]>;
}
