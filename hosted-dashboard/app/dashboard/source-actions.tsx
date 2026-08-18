"use client";

import { CommandMessage, useOwnerCommand } from "./command-client.tsx";
import { text, type RecordValue } from "./presentation.ts";

export default function SourceActions({ sources, connected }: { sources: RecordValue[]; connected: boolean }) {
  const { queue, message, busy } = useOwnerCommand();
  return <><CommandMessage message={message} /><div className="source-controls source-controls-page">{sources.map((source, index) => <div key={text(source, "id", String(index))}><span><strong>{text(source, "name", "Unnamed source")}</strong><small>{text(source, "operational_status", text(source, "health"))}</small></span><button className="button button-small" disabled={busy || !connected} onClick={() => queue("source.toggle", { source_id: text(source, "id", "") })}>{source.enabled === true ? "Disable" : "Enable"}</button></div>)}</div></>;
}
