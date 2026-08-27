/**
 * Verwaltet den `chappe rpc`-Kindprozess: Start, zeilenweises NDJSON über
 * stdin/stdout, Anfrage/Antwort-Korrelation über `id`, und ein zweistufiges,
 * plattformbewusstes Beenden.
 *
 * Alles, was hier passiert, ist Prozessführung — kein RPC-Methodenwissen.
 * app/main/ipc.ts baut darauf die konkreten Slice-0-Methoden (ping,
 * list_chats) auf.
 */

import { type ChildProcess, spawn } from "node:child_process";
import { resolveSidecarCommand } from "./paths";
import type { RpcIncomingMessage } from "../shared/protocol";

interface PendingCall {
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
}

/** Wie lange nach dem `shutdown`-Rahmen auf eine Antwort gewartet wird, bevor hart beendet wird. */
const SHUTDOWN_TIMEOUT_MS = 1500;
/** Wie lange nach SIGTERM auf das tatsächliche Prozessende gewartet wird, bevor SIGKILL folgt. */
const SIGTERM_GRACE_MS = 2000;

export class SidecarClient {
  private child: ChildProcess | null = null;
  private stdoutBuffer = "";
  private nextRequestId = 1;
  private readonly pending = new Map<number, PendingCall>();
  private exited = false;

  /** Startet den Sidecar-Prozess und verdrahtet stdout/stderr/Lifecycle-Events. */
  async start(dbPath: string): Promise<void> {
    const { command, args, env } = resolveSidecarCommand(dbPath);
    console.log(`[sidecar] starte: ${command} ${args.join(" ")}`);

    const child = spawn(command, args, {
      stdio: ["pipe", "pipe", "pipe"],
      // Unterdrückt das kurz aufblitzende Konsolenfenster unter Windows bei
      // jedem Start eines Konsolen-Binaries (docs/research/sidecar.md).
      windowsHide: true,
      env,
    });

    this.child = child;
    this.exited = false;
    this.stdoutBuffer = "";

    // Bekannter macOS-Fall: spawn() kann für ein PyInstaller-Binary eine
    // null-PID liefern, obwohl der Prozess tatsächlich läuft
    // (electron/electron#17074, siehe docs/research/sidecar.md). stdin/stdout
    // funktionieren davon unabhängig — nur ein späterer Kill-Versuch über
    // child.kill() ist dadurch nicht garantiert wirksam. Wir loggen das hier
    // sichtbar, statt uns stillschweigend auf child.kill() zu verlassen.
    if (typeof child.pid !== "number") {
      console.warn(
        "[sidecar] spawn() lieferte keine numerische PID (pid =", child.pid,
        "). Ein hartes Beenden über child.kill() ist dadurch nicht garantiert.",
      );
    }

    // UTF-8 hart erzwingen (Protokoll-Vertrag). setEncoding("utf-8") nutzt
    // intern einen StringDecoder, der einen an einer Byte-Grenze
    // zerschnittenen Mehrbyte-Codepunkt (z. B. ein Umlaut) über zwei
    // "data"-Ereignisse hinweg korrekt zusammensetzt — ein rohes
    // chunk.toString("utf-8") pro Ereignis würde das an ungünstiger Stelle
    // zerstören.
    child.stdout?.setEncoding("utf-8");
    child.stdout?.on("data", (chunk: string) => this.onStdoutChunk(chunk));

    child.stderr?.setEncoding("utf-8");
    child.stderr?.on("data", (chunk: string) => {
      // stderr ist ausschliesslich Diagnose und gehört NIE zum Protokoll —
      // wir geben es nur weiter, parsen es nie als NDJSON.
      console.error(`[sidecar:stderr] ${chunk.replace(/\n+$/, "")}`);
    });

    child.on("error", (error) => {
      console.error("[sidecar] Prozessfehler:", error);
      this.rejectAllPending(error);
    });

    child.on("exit", (code, signal) => {
      this.exited = true;
      console.log(`[sidecar] beendet (code=${code}, signal=${signal})`);
      this.rejectAllPending(
        new Error(`Sidecar-Prozess wurde beendet (code=${code}, signal=${signal})`),
      );
      this.child = null;
    });
  }

  /** true, solange ein Kindprozess-Handle gehalten wird (nicht zwingend noch am Leben, siehe pid-Hinweis oben). */
  get isRunning(): boolean {
    return this.child !== null;
  }

  /**
   * Schickt eine Anfrage und wartet auf die passende Antwort über `id`.
   * `timeoutMs` ist optional — die Slice-0-Methoden (ping, list_chats)
   * laufen ohne Timeout, der `shutdown`-Rahmen bekommt bewusst einen.
   */
  call<T = unknown>(
    method: string,
    params?: Record<string, unknown>,
    timeoutMs?: number,
  ): Promise<T> {
    const child = this.child;
    if (!child || !child.stdin || !child.stdin.writable) {
      return Promise.reject(
        new Error(`Sidecar nicht verbunden (Methode "${method}")`),
      );
    }

    const id = this.nextRequestId++;
    const line = JSON.stringify({ id, method, params }) + "\n";

    return new Promise<T>((resolve, reject) => {
      let timer: NodeJS.Timeout | undefined;

      const settle = (fn: () => void) => {
        if (timer) clearTimeout(timer);
        this.pending.delete(id);
        fn();
      };

      this.pending.set(id, {
        resolve: (value) => settle(() => resolve(value as T)),
        reject: (error) => settle(() => reject(error)),
      });

      if (timeoutMs) {
        timer = setTimeout(() => {
          this.pending.delete(id);
          reject(
            new Error(`Zeitüberschreitung bei Methode "${method}" (${timeoutMs} ms)`),
          );
        }, timeoutMs);
      }

      child.stdin!.write(line, "utf-8", (error) => {
        if (error) {
          this.pending.delete(id);
          if (timer) clearTimeout(timer);
          reject(error);
        }
      });
    });
  }

  /**
   * Zweistufiges Beenden, wie im Auftrag verlangt: erst der `shutdown`-Rahmen
   * über stdin mit kurzem Timeout, danach — nur falls der Prozess dann noch
   * lebt — hart, plattformabhängig. Wird ausschliesslich aus
   * `app.on("before-quit", …)` aufgerufen (app/main/index.ts), nie aus
   * `window-all-closed`, weil dieses Ereignis auf macOS beim Quit gar nicht
   * feuert.
   */
  async shutdown(): Promise<void> {
    const child = this.child;
    if (!child) return;

    try {
      await this.call("shutdown", undefined, SHUTDOWN_TIMEOUT_MS);
      // Die Antwort kam an, aber der Sidecar kann in diesem Moment noch
      // mitten in seinem eigenen sys.exit(0) stecken — eine kurze Karenzzeit,
      // bevor wir prüfen, ob wirklich noch hart nachgefasst werden muss.
      // Beobachtete Race im Praxistest: die RPC-Antwort war erfolgreich, das
      // node-seitige "exit"-Ereignis kam aber erst Millisekunden später an,
      // was hardKillIfStillAlive() sonst fälschlich zu einem überflüssigen
      // (wenn auch ungefährlichen) SIGTERM verleitet hätte.
      await this.waitForExit(300);
    } catch (error) {
      // Timeout oder Verbindungsfehler — kein Absturz, wir gehen unten zum
      // harten Beenden über. Auf Windows ist dieser Fall der Normalfall für
      // den kooperativen Teil: es gibt dort kein SIGTERM, der shutdown-Rahmen
      // selbst *ist* das einzige sanfte Signal.
      console.warn("[sidecar] shutdown-Anfrage ohne Erfolg:", (error as Error).message);
    }

    await this.hardKillIfStillAlive(child);
    this.child = null;
  }

  private async hardKillIfStillAlive(child: ChildProcess): Promise<void> {
    if (this.exited) {
      // Der Sidecar hat sich über den shutdown-Rahmen sauber selbst beendet
      // (Datenbankverbindung geschlossen, Exit-Code 0) — nichts mehr zu tun.
      return;
    }

    console.warn("[sidecar] reagiert nicht auf den shutdown-Rahmen — hartes Beenden.");

    if (typeof child.pid !== "number") {
      // Siehe Hinweis in start(): ohne gültige PID ist kein Kill-Weg
      // garantiert wirksam. child.kill() verpufft auf einem solchen Handle
      // laut docs/research/sidecar.md still, statt zu werfen — wir
      // versuchen es trotzdem als letzten Ausweg, protokollieren den Fall
      // aber explizit, statt ihn unbemerkt zu lassen.
      console.error(
        "[sidecar] child.pid ist keine Zahl — Beenden über PID nicht möglich, letzter Versuch über child.kill().",
      );
      try {
        child.kill();
      } catch (error) {
        console.error("[sidecar] child.kill() ohne gültige PID fehlgeschlagen:", error);
      }
      return;
    }

    if (process.platform === "win32") {
      // Windows kennt kein kooperatives SIGTERM — child.kill() liefe dort auf
      // ein sofortiges TerminateProcess() hinaus, ohne den vom
      // PyInstaller-Onefile-Bootloader gestarteten Kindprozess zu erfassen.
      // taskkill /T erfasst den ganzen Prozessbaum.
      await new Promise<void>((resolve) => {
        const killer = spawn(
          "taskkill",
          ["/pid", String(child.pid), "/T", "/F"],
          { windowsHide: true },
        );
        killer.on("exit", () => resolve());
        killer.on("error", (error) => {
          console.error("[sidecar] taskkill fehlgeschlagen:", error);
          resolve();
        });
      });
      return;
    }

    child.kill("SIGTERM");
    const exitedAfterTerm = await this.waitForExit(SIGTERM_GRACE_MS);
    if (!exitedAfterTerm) {
      console.warn("[sidecar] SIGTERM ohne Wirkung — SIGKILL.");
      child.kill("SIGKILL");
      await this.waitForExit(1000);
    }
  }

  private waitForExit(timeoutMs: number): Promise<boolean> {
    if (this.exited) return Promise.resolve(true);
    return new Promise<boolean>((resolve) => {
      const timer = setTimeout(() => {
        this.child?.off("exit", onExit);
        resolve(false);
      }, timeoutMs);
      const onExit = () => {
        clearTimeout(timer);
        resolve(true);
      };
      this.child?.once("exit", onExit);
    });
  }

  private onStdoutChunk(chunk: string): void {
    this.stdoutBuffer += chunk;

    // Eine JSON-Nachricht kann über mehrere "data"-Ereignisse eintreffen,
    // und mehrere Nachrichten können in einem Ereignis stecken — deshalb
    // ein Puffer, aus dem wiederholt vollständige Zeilen abgeschnitten
    // werden, statt pro "data"-Ereignis genau eine Nachricht anzunehmen.
    let newlineIndex: number;
    while ((newlineIndex = this.stdoutBuffer.indexOf("\n")) !== -1) {
      const rawLine = this.stdoutBuffer.slice(0, newlineIndex);
      this.stdoutBuffer = this.stdoutBuffer.slice(newlineIndex + 1);
      // Defensiv gegen CRLF, falls der Sidecar je auf einer Plattform landet,
      // die das einstreut — der Vertrag selbst verlangt nur "\n".
      const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
      if (line.length === 0) continue;
      this.handleLine(line);
    }
  }

  private handleLine(line: string): void {
    let message: RpcIncomingMessage;
    try {
      message = JSON.parse(line) as RpcIncomingMessage;
    } catch (error) {
      // Der Protokoll-Vertrag reserviert stderr für Diagnose — eine
      // ungültige JSON-Zeile auf stdout ist ein Vertragsbruch des Sidecars
      // selbst. Wir loggen sie und laufen weiter, statt die App abstürzen
      // zu lassen.
      console.error("[sidecar] ungültige JSON-Zeile auf stdout ignoriert:", line, error);
      return;
    }

    if ("event" in message) {
      // In Slice 0 definiert das Protokoll keine Ereignisse — wir
      // protokollieren sie trotzdem, statt sie stillschweigend zu verwerfen,
      // damit ein unerwartetes Ereignis in der Entwicklung auffällt.
      console.log(`[sidecar] Ereignis "${message.event}":`, message.data);
      return;
    }

    if (typeof message.id !== "number") {
      console.error("[sidecar] Antwort ohne numerische id ignoriert:", message);
      return;
    }

    const pending = this.pending.get(message.id);
    if (!pending) {
      console.warn(`[sidecar] Antwort auf unbekannte id ${message.id} ignoriert.`);
      return;
    }
    this.pending.delete(message.id);

    if (message.ok) {
      pending.resolve(message.result);
    } else {
      pending.reject(new Error(`[${message.error.code}] ${message.error.message}`));
    }
  }

  private rejectAllPending(error: Error): void {
    for (const [id, entry] of this.pending) {
      this.pending.delete(id);
      entry.reject(error);
    }
  }
}
