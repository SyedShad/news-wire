import { requirePageSession } from "@/lib/auth/session.ts";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { readSnapshot } from "@/lib/dashboard/store.ts";
import DashboardShell from "../dashboard-shell.tsx";
import { dateTime, text } from "../presentation.ts";

export const dynamic = "force-dynamic";

export default async function Drafts() {
  await requirePageSession(["master"]);
  const stored = await readSnapshot(runtimeEnv().DB);
  const drafts = stored?.snapshot.drafts || [];
  return <DashboardShell active="drafts" eyebrow="Content workspace" title="Content and history" intro="Current drafts, editable shells, generation requests, statuses, versions, and exports.">
    <section className="connection-strip"><span className={`status-pill ${stored?.bridgeConnected ? "status-pill-live" : "status-pill-waiting"}`}>{stored?.bridgeConnected ? "Live" : "Cached"}</span><span>Last synchronized {dateTime(stored?.receivedAt)}</span><strong>{drafts.length} entries</strong></section>
    <section className="data-panel"><div className="compact-list draft-list">{drafts.map((draft, index) => <article key={`${String(draft.entry_kind)}-${String(draft.id || index)}`}><div><strong>{draft.entry_kind === "draft" ? <a href={`/dashboard/drafts/${String(draft.id)}`}>{text(draft, "headline", text(draft, "story_headline", "Draft"))}</a> : text(draft, "story_headline", "Draft request")}</strong><span>{text(draft, "mode", String(draft.entry_kind || "Content"))} · {text(draft, "lane", "Unassigned")} · {text(draft, "approval_basis", "verified")}</span></div><div><span className="mini-status">{text(draft, "display_status", text(draft, "status"))}</span><time>{dateTime(draft.updated_at)}</time></div></article>)}{!drafts.length ? <p className="empty-state">No synchronized drafts or draft requests.</p> : null}</div></section>
  </DashboardShell>;
}
