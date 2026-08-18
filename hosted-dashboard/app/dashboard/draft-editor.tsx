"use client";

import { CommandMessage, useOwnerCommand } from "./command-client.tsx";
import { text, type RecordValue } from "./presentation.ts";

export default function DraftEditor({ draft, connected }: { draft: RecordValue; connected: boolean }) {
  const { queue, message, busy } = useOwnerCommand();
  return (
    <section className="owner-control-card">
      <div className="section-heading"><div><p className="eyebrow">Versioned editing</p><h2>Save a new draft version</h2></div></div>
      {!connected ? <p className="owner-warning">Editing is disabled until the laptop bridge is current.</p> : null}
      <CommandMessage message={message} />
      <form className="editor-form" onSubmit={(event) => {
        event.preventDefault();
        const data = new FormData(event.currentTarget);
        void queue("draft.save", { draft_id: Number(draft.id), headline: String(data.get("headline")), metadata: String(data.get("metadata") || ""), body: String(data.get("body")), lens: String(data.get("lens") || ""), suggested_flair: String(data.get("suggested_flair") || "") });
      }}>
        <label>Headline<input name="headline" required defaultValue={text(draft, "headline", "")} /></label>
        <label>Metadata<input name="metadata" defaultValue={text(draft, "metadata", "")} /></label>
        <label>Factual brief<textarea name="body" required defaultValue={text(draft, "body", "")} /></label>
        <label>Open-source lens<textarea name="lens" defaultValue={text(draft, "lens", "")} /></label>
        <label>Suggested flair<input name="suggested_flair" defaultValue={text(draft, "suggested_flair", "")} /></label>
        <button className="button button-small" disabled={busy || !connected}>Save new version</button>
      </form>
    </section>
  );
}
