"use client";

import { useState } from "react";
import type { JsonValue, OwnerCommand } from "@/lib/dashboard/types.ts";

type RecordValue = Record<string, JsonValue>;

function value(record: RecordValue, key: string): string {
  const result = record[key];
  return typeof result === "string" ? result : "";
}

function records(record: RecordValue, key: string): RecordValue[] {
  const result = record[key];
  return Array.isArray(result)
    ? result.filter((item): item is RecordValue => Boolean(item) && typeof item === "object" && !Array.isArray(item))
    : [];
}

export default function OwnerControls(props: {
  stories: RecordValue[];
  drafts: RecordValue[];
  sources: RecordValue[];
  schedule: RecordValue;
  initialCommands: OwnerCommand[];
  connected: boolean;
}) {
  const [commands, setCommands] = useState(props.initialCommands);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function queue(operation: string, payload: RecordValue, confirmMessage?: string) {
    if (confirmMessage && !window.confirm(confirmMessage)) return;
    setBusy(true);
    setMessage("");
    try {
      const response = await fetch("/api/owner/commands", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ operation, payload }),
      });
      const result = await response.json() as { ok?: boolean; command?: OwnerCommand; error?: string };
      if (!response.ok || !result.command) throw new Error(result.error || "The command could not be queued");
      setCommands((current) => [result.command!, ...current].slice(0, 30));
      setMessage("Command queued. The local bridge will run it shortly.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "The command could not be queued");
    } finally {
      setBusy(false);
    }
  }

  async function refreshCommands() {
    const response = await fetch("/api/owner/commands", { cache: "no-store" });
    const result = await response.json() as { commands?: OwnerCommand[] };
    if (result.commands) setCommands(result.commands);
  }

  const editableDrafts = props.drafts.filter((draft) =>
    draft.entry_kind === "draft" && (draft.status === "Current" || draft.status === "Editable Shell"),
  );

  return (
    <section className="owner-control-card" id="owner-controls" aria-labelledby="owner-controls-heading">
      <div className="section-heading">
        <div><p className="eyebrow">Owner only</p><h2 id="owner-controls-heading">Full operational access</h2></div>
        <span className="role-pill role-pill-full_control">Master role</span>
      </div>
      <p className="section-copy">Commands are authenticated here, queued on the hosted service, and executed by the outbound local bridge.</p>
      {!props.connected ? <p className="owner-warning">The bridge is offline. Operational controls are disabled so actions cannot run unexpectedly after the laptop reconnects.</p> : null}
      {message ? <p className="owner-message" role="status">{message}</p> : null}

      <div className="owner-sections">
        <details open>
          <summary>Schedule and scans</summary>
          <div className="owner-section-body">
            <div className="button-row">
              {["run_now", "pause", "resume", "install"].map((action) => (
                <button className="button button-small" disabled={busy || !props.connected} key={action} onClick={() => queue("schedule.action", { action })}>
                  {action.replaceAll("_", " ")}
                </button>
              ))}
              <button className="button button-small button-danger" disabled={busy || !props.connected} onClick={() => queue("schedule.action", { action: "uninstall" }, "Uninstall the local schedule?")}>uninstall</button>
            </div>
            <form className="inline-form" onSubmit={(event) => {
              event.preventDefault();
              const data = new FormData(event.currentTarget);
              void queue("schedule.catch_up", { start: String(data.get("start")), end: String(data.get("end")) });
            }}>
              <label>Catch up from <input name="start" type="date" required defaultValue={value(props.schedule, "extended_start_suggestion")} /></label>
              <label>through <input name="end" type="date" required defaultValue={value(props.schedule, "today")} /></label>
              <button className="button button-small" disabled={busy || !props.connected}>Queue catch-up</button>
            </form>
          </div>
        </details>

        <details>
          <summary>Review and content</summary>
          <div className="owner-section-body owner-story-actions">
            {props.stories.slice(0, 50).map((story, index) => (
              <article key={value(story, "id") || String(index)}>
                <strong>{value(story, "headline") || "Untitled story"}</strong>
                <div className="button-row">
                  <button className="button button-small" disabled={busy || !props.connected} onClick={() => queue("story.content.create", { story_id: value(story, "id"), guidance: "" })}>Create content</button>
                  <button className="button button-small" disabled={busy || !props.connected} onClick={() => queue("story.draft.retry", { story_id: value(story, "id") })}>Retry draft</button>
                  <button className="button button-small" disabled={busy || !props.connected} onClick={() => queue("story.review", { story_id: value(story, "id"), action: "archive", reason: "Archived through owner dashboard" }, "Archive this story?")}>Archive</button>
                  <button className="button button-small button-danger" disabled={busy || !props.connected} onClick={() => queue("story.review", { story_id: value(story, "id"), action: "withdraw", reason: "Withdrawn through owner dashboard" }, "Withdraw this story?")}>Withdraw</button>
                </div>
                <form className="inline-form" onSubmit={(event) => {
                  event.preventDefault();
                  const data = new FormData(event.currentTarget);
                  void queue("story.evidence.inspect", {
                    story_id: value(story, "id"),
                    url: String(data.get("url")),
                    acquisition_method: "manual",
                  });
                  event.currentTarget.reset();
                }}>
                  <label className="grow">Add evidence URL <input name="url" type="url" required placeholder="https://…" /></label>
                  <button className="button button-small" disabled={busy || !props.connected}>Inspect</button>
                </form>
                {records(story, "evidence_sources").map((evidence, evidenceIndex) => {
                  const evidenceId = Number(evidence.id);
                  const evidenceStatus = value(evidence, "status");
                  const claims = records(story, "claims");
                  return (
                    <details className="evidence-control" key={String(evidence.id || evidenceIndex)}>
                      <summary>{value(evidence, "title") || value(evidence, "canonical_url") || `Evidence ${evidenceId}`} · {evidenceStatus}</summary>
                      <div>
                        {evidenceStatus === "fetched" || evidenceStatus === "confirmed" ? (
                          <form onSubmit={(event) => {
                            event.preventDefault();
                            const data = new FormData(event.currentTarget);
                            const relationships: Record<string, string> = {};
                            for (const claim of claims) {
                              const claimId = String(claim.id);
                              const relationship = String(data.get(`claim_${claimId}`) || "");
                              if (relationship) relationships[claimId] = relationship;
                            }
                            void queue("story.evidence.confirm", {
                              story_id: value(story, "id"),
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
                              <label>Role<select name="role" defaultValue={value(evidence, "confirmed_role") || value(evidence, "proposed_role") || "Reporting"}><option>Event</option><option>Reporting</option><option>Discovery</option></select></label>
                              <label>Reporting provenance<select name="provenance_type" defaultValue={value(evidence, "provenance_type") || "original"}><option value="original">Original</option><option value="syndicated">Syndicated</option><option value="cites">Cites another publisher</option></select></label>
                              <label className="checkbox-label"><input name="first_party" type="checkbox" defaultChecked={Boolean(evidence.first_party_confirmed)} /> First party</label>
                            </div>
                            <div className="claim-map">
                              {claims.map((claim, claimIndex) => (
                                <label key={String(claim.id || claimIndex)}>{value(claim, "text") || `Claim ${String(claim.id)}`}<select name={`claim_${String(claim.id)}`} defaultValue=""><option value="">Not mapped</option><option value="supports">Directly supports</option><option value="attributes">Reports or attributes</option><option value="context">Context</option><option value="contradicts">Contradicts</option></select></label>
                              ))}
                            </div>
                            <div className="inline-form">
                              <label>Origin name<input name="origin_name" defaultValue={value(evidence, "reporting_origin_name")} /></label>
                              <label className="grow">Origin URL<input name="origin_url" type="url" defaultValue={value(evidence, "reporting_origin_url")} /></label>
                              <label className="grow">Reason<input name="reason" defaultValue={value(evidence, "confirmation_reason")} /></label>
                            </div>
                            <div className="button-row">
                              <button className="button button-small" disabled={busy || !props.connected}>Confirm evidence</button>
                              <button className="button button-small button-danger" type="button" disabled={busy || !props.connected} onClick={() => queue("story.evidence.exclude", { story_id: value(story, "id"), evidence_id: evidenceId, reason: "Excluded through owner dashboard" }, "Exclude this evidence source?")}>Exclude</button>
                            </div>
                          </form>
                        ) : <p className="empty-state">This source must finish inspection before it can be confirmed.</p>}
                      </div>
                    </details>
                  );
                })}
              </article>
            ))}
          </div>
        </details>

        <details>
          <summary>Edit current drafts</summary>
          <div className="owner-section-body owner-drafts">
            {editableDrafts.slice(0, 20).map((draft, index) => (
              <form key={String(draft.id || index)} onSubmit={(event) => {
                event.preventDefault();
                const data = new FormData(event.currentTarget);
                void queue("draft.save", {
                  draft_id: Number(draft.id),
                  headline: String(data.get("headline")),
                  metadata: String(data.get("metadata")),
                  body: String(data.get("body")),
                  lens: String(data.get("lens")),
                  suggested_flair: String(data.get("suggested_flair")),
                });
              }}>
                <label>Headline<input name="headline" required defaultValue={value(draft, "headline")} /></label>
                <label>Metadata<input name="metadata" defaultValue={value(draft, "metadata")} /></label>
                <label>Factual brief<textarea name="body" required defaultValue={value(draft, "body")} /></label>
                <label>Open-source lens<textarea name="lens" defaultValue={value(draft, "lens")} /></label>
                <label>Suggested flair<input name="suggested_flair" defaultValue={value(draft, "suggested_flair")} /></label>
                <button className="button button-small" disabled={busy || !props.connected}>Save new version</button>
              </form>
            ))}
            {!editableDrafts.length ? <p className="empty-state">No editable drafts are synchronized.</p> : null}
          </div>
        </details>

        <details>
          <summary>Source management</summary>
          <div className="owner-section-body source-controls">
            {props.sources.map((source, index) => (
              <div key={value(source, "id") || String(index)}>
                <span><strong>{value(source, "name")}</strong><small>{value(source, "operational_status") || value(source, "health")}</small></span>
                <button className="button button-small" disabled={busy || !props.connected} onClick={() => queue("source.toggle", { source_id: value(source, "id") })}>
                  {source.enabled ? "Disable" : "Enable"}
                </button>
              </div>
            ))}
          </div>
        </details>

        <details>
          <summary>Administration</summary>
          <div className="owner-section-body">
            <div className="button-row">
              <button className="button button-small" disabled={busy || !props.connected} onClick={() => queue("alerts.mark_read", {})}>Mark alerts read</button>
              <button className="button button-small button-danger" disabled={busy || !props.connected} onClick={() => queue("diagnostics.purge", { confirmation: "PURGE OPERATIONS", category_selected: true }, "Permanently purge operational diagnostics?")}>Purge diagnostics</button>
            </div>
          </div>
        </details>

        <details>
          <summary>Recent command activity</summary>
          <div className="owner-section-body">
            <button className="button button-small" type="button" onClick={() => void refreshCommands()}>Refresh</button>
            <div className="command-list">
              {commands.map((command) => (
                <div key={command.id}><strong>{command.operation}</strong><span className={`command-${command.status}`}>{command.status}</span><small>{command.error || new Date(command.createdAt).toLocaleString()}</small></div>
              ))}
              {!commands.length ? <p className="empty-state">No owner commands have been queued.</p> : null}
            </div>
          </div>
        </details>
      </div>
    </section>
  );
}
