import type { ChappeApi } from "../../shared/protocol";

declare global {
  interface Window {
    /** Von app/preload/index.ts über contextBridge freigegeben. */
    chappeAPI: ChappeApi;
  }
}

export {};
