<script setup lang="ts">
/**
 * Eine einzige ungestylte Liste — bewusst hässlich. Design kommt erst in
 * Slice 3 (Design-Fundament), siehe docs/gui-plan.md. Zweck hier ist
 * ausschliesslich der Nachweis, dass die Sidecar-Kette trägt: ping()
 * bestätigt die Verbindung, listChats() zeigt echte Chats aus einer
 * vorhandenen Datenbank.
 */
import { onMounted, ref } from "vue";
import type { ChatSummary, PingResult } from "../../shared/protocol";

const ping = ref<PingResult | null>(null);
const chats = ref<ChatSummary[]>([]);
const error = ref<string | null>(null);
const loading = ref(true);

onMounted(async () => {
  try {
    ping.value = await window.chappeAPI.ping();
    chats.value = await window.chappeAPI.listChats();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <div>
    <h1>Chappe — Durchstich (Slice 0)</h1>

    <p v-if="ping">
      Sidecar erreichbar: chappe {{ ping.version }}, Protokoll {{ ping.protocol }}
    </p>

    <p v-if="loading">Lade Chats …</p>
    <p v-else-if="error">Fehler: {{ error }}</p>
    <p v-else-if="chats.length === 0">Keine Chats gefunden.</p>

    <ul v-else>
      <li v-for="(chat, index) in chats" :key="String(chat.chat_id ?? chat.chat ?? index)">
        {{ chat.chat ?? "(ohne Namen)" }} — {{ chat.messages ?? "?" }} Nachrichten
      </li>
    </ul>
  </div>
</template>
