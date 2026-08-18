"use client";

import type { JsonValue } from "@/lib/dashboard/types.ts";
import { CommandMessage, useOwnerCommand } from "./command-client.tsx";
import { records, text, type RecordValue } from "./presentation.ts";

export default function StoryActions({ story, connected }: { story: RecordValue; connected: boolean }) {
  const { queue, message, busy } = useOwnerCommand();
  const disabled = busy || !connected;
  const storyId = text(story, "id", "");
  const claims = records(story, "claims");
  const evidence = records(story, "evidence_sources");
  return (
    <section className="owner-control-card">
      <div className="section-heading"><div><p className="eyebrow">Owner operations</p><h2>Review and evidence</h2></div><span className={`status-pill ${connected ? "status-pill-live" : "status-pill-waiting"}`}>{connected ? "Ready" : "Laptop offline"}</span></div>
      {!connected ? <p className="owner-warning">Actions require a current laptop heartbeat and are disabled while cached data is shown.</p> : null}
      <CommandMessage message={message} />
      <div className="button-row control-actions">
        <button className="button button-small" disabled={disabled} onClick={() => queue("story.content.create", { story_id: storyId, guidance: "" })}>Create content</button>
        <button className="button button-small" disabled={disabled} onClick={() => queue("story.draft.retry", { story_id: storyId })}>Retry draft</button>
        <button className="button button-small" disabled={disabled} onClick={() => queue("story.review", { story_id: storyId, action: "archive", reason: "Archived through owner dashboard" }, "Archive this story?")}>Archive</button>
        <button className="button button-small button-danger" disabled={disabled} onClick={() => queue("story.review", { story_id: storyId, action: "withdraw", reason: "Withdrawn through owner dashboard" }, "Withdraw this story?")}>Withdraw</button>
      </div>
      <form className="inline-form" onSubmit={(event) => {
        event.preventDefault();
        const data = new FormData(event.currentTarget);
        void queue("story.evidence.inspect", { story_id: storyId, url: String(data.get("url")), acquisition_method: "manual" });
        event.currentTarget.reset();
      }}>
        <label className="grow">Inspect evidence URL<input name="url" type="url" required placeholder="https://…" /></label>
        <button className="button button-small" disabled={disabled}>Inspect</button>
      </form>
      <div className="owner-sections">
        {evidence.map((item, index) => {
          const evidenceId = Number(item.id);
          return (
            <details className="evidence-control" key={String(item.id || index)}>
              <summary>{text(item, "title", text(item, "canonical_url", `Evidence ${evidenceId}`))} · {text(item, "status")}</summary>
              <div className="owner-section-body">
                <p className="empty-state">{text(item, "excerpt", "No excerpt was retained.")}</p>
                <form onSubmit={(event) => {
                  event.preventDefault();
                  const data = new FormData(event.currentTarget);
                  const relationships: Record<string, JsonValue> = {};
                  for (const claim of claims) {
                    const claimId = String(claim.id);
                    const relationship = String(data.get(`claim_${claimId}`) || "");
                    if (relationship) relationships[claimId] = relationship;
                  }
                  void queue("story.evidence.confirm", {
                    story_id: storyId,
                    evidence_id: evidenceId,
                    role: String(data.get("role")),
                    relationships,
                    first_party: data.get("first_party") === "on",
                    reason: String(data.get("reason") || ""),
                    provenance_type: String(data.get("provenance_type") || "original"),
                    origin_name: String(data.get("origin_name") || ""),
                    origin_url: String(data.get("origin_url") || ""),
                  });
                }}>
                  <div className="inline-form">
                    <label>Role<select name="role" defaultValue={text(item, "confirmed_role", text(item, "proposed_role", "Reporting"))}><option>Event</option><option>Reporting</option><option>Discovery</option></select></label>
                    <label>Provenance<select name="provenance_type" defaultValue={text(item, "provenance_type", "original")}><option value="original">Original</option><option value="syndicated">Syndicated</option><option value="cites">Cites publisher</option></select></label>
                    <label className="checkbox-label"><input name="first_party" type="checkbox" defaultChecked={item.first_party_confirmed === true} /> First party</label>
                  </div>
                  <div className="claim-map">{claims.map((claim, claimIndex) => <label key={String(claim.id || claimIndex)}>{text(claim, "text", `Claim ${String(claim.id)}`)}<select name={`claim_${String(claim.id)}`} defaultValue=""><option value="">Not mapped</option><option value="supports">Directly supports</option><option value="attributes">Reports or attributes</option><option value="context">Context</option><option value="contradicts">Contradicts</option></select></label>)}</div>
                  <div className="inline-form"><label>Origin name<input name="origin_name" defaultValue={text(item, "reporting_origin_name", "")} /></label><label className="grow">Origin URL<input name="origin_url" type="url" defaultValue={text(item, "reporting_origin_url", "")} /></label><label className="grow">Reason<input name="reason" defaultValue={text(item, "confirmation_reason", "")} /></label></div>
                  <div className="button-row"><button className="button button-small" disabled={disabled}>Confirm evidence</button><button className="button button-small button-danger" type="button" disabled={disabled} onClick={() => queue("story.evidence.exclude", { story_id: storyId, evidence_id: evidenceId, reason: "Excluded through owner dashboard" }, "Exclude this evidence source?")}>Exclude</button></div>
                </form>
              </div>
            </details>
          );
        })}
        {!evidence.length ? <p className="empty-state">No inspected evidence sources.</p> : null}
      </div>
    </section>
  );
}
