import { requirePageSession } from "@/lib/auth/session.ts";
import Link from "next/link";
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
  return <DashboardShell active="drafts" eyebrow="Draft detail" title="" intro="" customHeader>
    <nav className="breadcrumb" aria-label="Breadcrumb"><Link href="/dashboard/drafts">Content</Link><span>›</span><span>Version {String(draft.version || "—")}</span></nav>
    <header className="page-header draft-header"><div><p className="kicker">{text(draft, "mode", "Draft")}</p><h1>Edit version {String(draft.version || "—")}</h1><p className="lede">Saving creates a new version and preserves this one in history.</p></div><div className="header-actions"><a className="button button-secondary" href={`/api/dashboard/drafts/${id}/export-html`}>Export HTML</a><a className="button button-dark" href={`/api/dashboard/drafts/${id}/export`}>Export Markdown</a></div></header>
    <div className="editor-layout"><DraftEditor draft={draft} connected={Boolean(stored?.bridgeConnected)} /><aside className="editor-rail">
      <section className="panel"><p className="section-index rail-heading">Source-bound content</p><h2 className="rail-title">{text(draft, "story_headline", "Story")}</h2><dl className="detail-list"><div><dt>Lane</dt><dd>{text(draft, "lane")}</dd></div><div><dt>Priority</dt><dd>{text(draft, "priority")}</dd></div><div><dt>Freshness</dt><dd>{text(draft, "freshness")}</dd></div><div><dt>Story state</dt><dd>{text(draft, "story_status", "unknown").replaceAll("_", " ")}</dd></div><div><dt>Correction status</dt><dd>{records(draft, "corrections").length ? `${records(draft, "corrections").length} needs review` : "Clear"}</dd></div></dl></section>
      <section className="panel"><p className="section-index rail-heading">Version history</p><div className="version-list">{records(draft, "history").map((item, index) => <a className={String(item.id) === id ? "current" : ""} href={`/dashboard/drafts/${String(item.id)}`} key={String(item.id || index)}><span>v{String(item.version)} · {text(item, "status")}</span><small>{dateTime(item.updated_at)}</small></a>)}</div></section>
      <section className="panel comparison-card"><p className="section-index rail-heading">Version comparison</p>{comparison ? <><h2 className="rail-title">v{String(draft.version)} ↔ v{String(comparison.version || "—")}</h2><p className="comparison-note">{text(comparison, "direction", "Adjacent")} version · {dateTime(comparison.updated_at)}</p><a className="button button-secondary comparison-link" href={`/dashboard/drafts/${String(comparison.id)}`}>Open {text(comparison, "direction", "adjacent").toLowerCase()} version</a></> : <><h2 className="rail-title">No adjacent version</h2><p className="comparison-note">Save a revision to compare field-level changes while preserving this version.</p></>}</section>
      <section className="panel"><p className="section-index rail-heading">Sources</p><ul className="plain-list">{records(draft, "source_rows").map((item, index) => <li key={String(index)}><a href={text(item, "url", "#")} target="_blank" rel="noreferrer">{text(item, "title", "Source")}</a>{text(item, "role", "") ? ` — ${text(item, "role", "")}` : ""}</li>)}</ul></section>
    </aside></div>
  </DashboardShell>;
}
