"use client";

import { CommandMessage, useOwnerCommand } from "./command-client.tsx";

export default function NoticeActions({ connected, legacyBridge = false }: { connected: boolean;legacyBridge?:boolean }) {
  const { queue, message, busy } = useOwnerCommand();
  return (
    <>
      <button className="button button-secondary" type="button" disabled={busy || !connected} onClick={() => queue(legacyBridge ? "alerts.mark_read" : "operations.mark_read", {})}>Mark notices read</button>
      <CommandMessage message={message} />
    </>
  );
}
