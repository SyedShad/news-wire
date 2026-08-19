"use client";

import { CommandMessage, useOwnerCommand } from "./command-client.tsx";
import { text, type RecordValue } from "./presentation.ts";

export default function DraftEditor({ draft, connected }: { draft: RecordValue; connected: boolean }) {
  const { queue, message, busy } = useOwnerCommand();
  return (
    <form className="panel editor" onSubmit={(event) => {
      event.preventDefault();
      const data = new FormData(event.currentTarget);
      void queue("draft.save", { draft_id: Number(draft.id), headline: String(data.get("headline")), metadata: String(data.get("metadata") || ""), body: String(data.get("body")), lens: String(data.get("lens") || ""), suggested_flair: String(data.get("suggested_flair") || "") });
    }}>
      {!connected ? <p className="owner-warning">Editing is disabled until the laptop bridge is current.</p> : null}
      <CommandMessage message={message} />
      <label>Title<input name="headline" required maxLength={300} defaultValue={text(draft, "headline", "")} /></label>
      <label>Metadata<input name="metadata" maxLength={500} defaultValue={text(draft, "metadata", "")} /></label>
      <label>Factual brief <span>confirmed source links use portable Markdown</span><textarea name="body" rows={16} required defaultValue={text(draft, "body", "")} /></label>
      {text(draft, "mode", "") === "Open-Source Lens Brief" || text(draft, "lens", "") ? <label>Open-source lens <span>approved analysis section</span><textarea name="lens" rows={6} defaultValue={text(draft, "lens", "")} /></label> : <input type="hidden" name="lens" value="" />}
      <label>Suggested flair<input name="suggested_flair" maxLength={80} defaultValue={text(draft, "suggested_flair", "")} /></label>
      <div className="editor-actions"><span>Current status: <strong>{text(draft, "status", "unknown")}</strong></span><button className="button button-primary" disabled={busy || !connected}>Save as version {Number(draft.version || 0) + 1}</button></div>
    </form>
  );
}
