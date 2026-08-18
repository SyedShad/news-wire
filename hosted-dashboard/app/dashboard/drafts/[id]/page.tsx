import { requirePageSession } from "@/lib/auth/session.ts";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { readDetail, readSnapshot, requestDetail } from "@/lib/dashboard/store.ts";
import DashboardShell from "../../dashboard-shell.tsx";
import PendingDetail from "../../pending-detail.tsx";
import DraftEditor from "../../draft-editor.tsx";
import { dateTime, records, text } from "../../presentation.ts";

export const dynamic = "force-dynamic";

export default async function DraftDetail({ params }: { params: Promise<{ id: string }> }) {
  const session = await requirePageSession(["master"]);
  const { id } = await params;
  const source = runtimeEnv();
  const stored = await readSnapshot(source.DB);
  const draft = await readDetail(source.DB, "draft", id);
  if (!draft && stored?.bridgeConnected && /^\d+$/u.test(id)) await requestDetail(source.DB, { resourceType: "draft", resourceId: id, requestedBy: session.actorId });
  if (!draft) return <DashboardShell active="drafts" eyebrow="Draft detail" title="Fetching draft" intro="Full draft bodies and version comparisons are loaded from the laptop only when requested.">{stored?.bridgeConnected ? <PendingDetail label="Retrieving draft, sources, and version history" /> : <section className="data-panel"><h2>Draft unavailable while offline</h2><p className="empty-state">Wake the laptop and reload this page.</p></section>}</DashboardShell>;
  const comparison = draft.comparison && typeof draft.comparison === "object" && !Array.isArray(draft.comparison) ? draft.comparison : undefined;
  return <DashboardShell active="drafts" eyebrow={`${text(draft, "mode", "Draft")} · Version ${String(draft.version || "—")}`} title={text(draft, "headline", "Untitled draft")} intro={`${text(draft, "story_headline", "Story")} · ${text(draft, "status", "Unknown status")}`}>
    <section className="detail-summary"><dl><div><dt>Lane</dt><dd>{text(draft, "lane")}</dd></div><div><dt>Priority</dt><dd>{text(draft, "priority")}</dd></div><div><dt>Approval</dt><dd>{text(draft, "approval_basis")}</dd></div><div><dt>Updated</dt><dd>{dateTime(draft.updated_at)}</dd></div></dl><div className="button-row"><a className="button button-small" href={`/api/dashboard/drafts/${id}/export`}>Markdown export</a><a className="button button-small" href={`/api/dashboard/drafts/${id}/export-html`}>HTML export</a></div></section>
    <section className="draft-document"><p className="draft-metadata">{text(draft, "metadata", "")}</p><div className="draft-body">{text(draft, "body", "No draft body.")}</div>{text(draft, "lens", "") ? <><h2>Open-source lens</h2><div className="draft-body">{text(draft, "lens", "")}</div></> : null}</section>
    <section className="split-panels"><div className="data-panel"><div className="section-heading"><div><p className="eyebrow">Version history</p><h2>Saved versions</h2></div></div><div className="record-list">{records(draft, "history").map((item, index) => <article key={String(item.id || index)}><strong><a href={`/dashboard/drafts/${String(item.id)}`}>Version {String(item.version)}</a></strong><span>{text(item, "status")}</span><small>{dateTime(item.updated_at)}</small></article>)}</div></div><div className="data-panel"><div className="section-heading"><div><p className="eyebrow">Comparison</p><h2>Superseded version</h2></div></div>{comparison ? <div className="record-list"><article><strong>{text(comparison, "headline", "Previous draft")}</strong><span>{text(comparison, "status", "")}</span><small>{dateTime(comparison.updated_at)}</small></article></div> : <p className="empty-state">This version does not supersede another draft.</p>}<div className="record-list">{records(draft, "corrections").map((item, index) => <article key={String(index)}><strong>{text(item, "title", "Correction")}</strong><span>{text(item, "body", "")}</span><small>{dateTime(item.created_at)}</small></article>)}</div></div></section>
    <section className="data-panel"><div className="section-heading"><div><p className="eyebrow">Sources</p><h2>Draft citations</h2></div></div><div className="record-list">{records(draft, "source_rows").map((item, index) => <article key={String(index)}><strong>{text(item, "title", "Source")}</strong><a href={text(item, "url", "#")} target="_blank" rel="noreferrer">{text(item, "url", "Open")}</a></article>)}</div></section>
    <DraftEditor draft={draft} connected={Boolean(stored?.bridgeConnected)} />
  </DashboardShell>;
}
