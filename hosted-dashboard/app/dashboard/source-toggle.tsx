"use client";

import { useOwnerCommand } from "./command-client.tsx";

export default function SourceToggle({ id, name, enabled, connected }: { id: string; name: string; enabled: boolean; connected: boolean }) {
  const { queue, busy } = useOwnerCommand();
  return <button className={`toggle ${enabled ? "on" : ""}`} type="button" disabled={busy || !connected} aria-label={`${enabled ? "Disable" : "Enable"} ${name}`} onClick={() => queue("source.toggle", { source_id: id })}><span /></button>;
}
