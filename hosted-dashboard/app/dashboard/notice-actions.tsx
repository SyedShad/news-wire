"use client";

import { CommandMessage, useOwnerCommand } from "./command-client.tsx";

export default function NoticeActions({ connected }: { connected: boolean }) {
  const { queue, message, busy } = useOwnerCommand();
  return (
    <>
      <button className="button button-secondary" type="button" disabled={busy || !connected} onClick={() => queue("alerts.mark_read", {})}>Mark notices read</button>
      <CommandMessage message={message} />
    </>
  );
}
