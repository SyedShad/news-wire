import { requirePageSession } from "@/lib/auth/session.ts";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { readDetail, readSnapshot, requestDetail } from "@/lib/dashboard/store.ts";
import DashboardShell from "../../dashboard-shell.tsx";
import PendingDetail from "../../pending-detail.tsx";
import StoryActions from "../../story-actions.tsx";
import { dateTime, records, text } from "../../presentation.ts";

export const dynamic = "force-dynamic";

export default async function StoryDetail({ params }: { params: Promise<{ id: string }> }) {
  const session = await requirePageSession(["master"]);
  const { id } = await params;
  const source = runtimeEnv();
  const stored = await readSnapshot(source.DB);
  const story = stored?.snapshot.stories.find((item) => String(item.id) === id) || await readDetail(source.DB, "story", id);
  if (!story && stored?.bridgeConnected) await requestDetail(source.DB, { resourceType: "story", resourceId: id, requestedBy: session.actorId });
  if (!story) return <DashboardShell active="inbox" eyebrow="Story detail" title="Fetching story" intro="Historical details are requested from the laptop only when needed.">{stored?.bridgeConnected ? <PendingDetail label="Retrieving the full story, evidence, and history" /> : <section className="data-panel"><h2>Detail unavailable while offline</h2><p className="empty-state">The story index remains cached, but this historical detail was not retained. Wake the laptop and reload.</p></section>}</DashboardShell>;
  const sources = records(story, "sources");
  const claims = records(story, "claims");
  const evidence = records(story, "evidence_sources");
  return <DashboardShell active="inbox" eyebrow={`${text(story, "priority", "Standard")} · ${text(story, "lane", "Unassigned")}`} title={text(story, "headline", "Untitled story")} intro={text(story, "summary", "Full review detail synchronized from the laptop.")}>
    <section className="detail-summary"><dl><div><dt>Status</dt><dd>{text(story, "status")}</dd></div><div><dt>Freshness</dt><dd>{text(story, "freshness")}</dd></div><div><dt>Research</dt><dd>{text(story, "research_status")}</dd></div><div><dt>Detected</dt><dd>{dateTime(story.detected_at)}</dd></div></dl><div className="button-row"><a className="button button-small" href={`/api/dashboard/stories/${encodeURIComponent(id)}/evidence`}>Evidence JSON</a><a className="button button-small" href="/dashboard/inbox">Back to inbox</a></div></section>
    <section className="split-panels"><div className="data-panel"><div className="section-heading"><div><p className="eyebrow">Claims</p><h2>Claim map</h2></div><span className="panel-count">{claims.length}</span></div><div className="record-list">{claims.map((claim, index) => <article key={String(claim.id || index)}><strong>{text(claim, "text", `Claim ${index + 1}`)}</strong><span>{text(claim, "evidence_relationships", "No mapped relationship")}</span><small>{text(claim, "evidence_sources", "No mapped source")}</small></article>)}</div></div><div className="data-panel"><div className="section-heading"><div><p className="eyebrow">Content history</p><h2>Drafts and actions</h2></div></div><div className="record-list">{records(story, "drafts").map((draft, index) => <article key={String(draft.id || index)}><strong><a href={`/dashboard/drafts/${String(draft.id)}`}>{text(draft, "headline", `Draft v${String(draft.version)}`)}</a></strong><span>Version {String(draft.version)} · {text(draft, "status")}</span><small>{dateTime(draft.updated_at)}</small></article>)}{records(story, "actions").map((action, index) => <article key={`action-${String(action.id || index)}`}><strong>{text(action, "action", "Review action")}</strong><span>{text(action, "reason", "No reason recorded")}</span><small>{dateTime(action.created_at)}</small></article>)}</div></div></section>
    <section className="data-panel"><div className="section-heading"><div><p className="eyebrow">Sources</p><h2>Discovery and evidence</h2></div><span className="panel-count">{sources.length + evidence.length}</span></div><div className="source-table">{[...sources, ...evidence].map((item, index) => <article key={`${String(item.id || index)}-${index}`}><div><strong>{text(item, "title", text(item, "source_name", "Source"))}</strong><a href={text(item, "final_url", text(item, "canonical_url", text(item, "url", "#")))} target="_blank" rel="noreferrer">{text(item, "publisher", text(item, "source_name", "Open source"))}</a></div><span>{text(item, "confirmed_role", text(item, "source_role", text(item, "status")))}</span><time>{dateTime(item.published_at)}</time></article>)}</div></section>
    <section className="data-panel"><div className="section-heading"><div><p className="eyebrow">Research</p><h2>Research attempts</h2></div></div><div className="record-list">{records(story, "research_attempts").map((attempt, index) => <article key={String(attempt.id || index)}><strong>{text(attempt, "status", "Attempt")}</strong><span>{text(attempt, "failure_reason", text(attempt, "query", ""))}</span><small>{dateTime(attempt.created_at)}</small></article>)}{!records(story, "research_attempts").length ? <p className="empty-state">No research attempts recorded.</p> : null}</div></section>
    <StoryActions story={story} connected={Boolean(stored?.bridgeConnected)} />
  </DashboardShell>;
}
