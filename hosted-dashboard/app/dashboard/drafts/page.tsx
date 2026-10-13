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
  const jobs = stored?.snapshot.generation_jobs || [];
  return <DashboardShell active="drafts" eyebrow="Manual publishing workspace" title="Content & history" intro="Every revision is a new version. You make the final posting decision.">
    <section className="panel"><div className="panel-heading"><h2>Generated content and graphics</h2><span>{jobs.length} versions</span></div><div className="table-list">{jobs.map((job,index) => <a className="table-row" key={String(job.id || index)} href={`/dashboard/articles/${encodeURIComponent(String(job.article_id))}`}><div><span className="tag tag-outline">{job.task_type === "graphics" ? "Graphics" : "Content"}</span></div><div className="table-main"><strong>{text(job,"status","queued").replaceAll("_"," ")}</strong><small>{text(job,"model","")}</small></div><div className="table-date">{dateTime(job.updated_at)}<span>→</span></div></a>)}{!jobs.length ? <p className="empty-state">No generated outputs yet.</p> : null}</div></section>
    <section className="panel alert-panel"><h2 className="history-heading">Earlier drafts</h2><div className="table-list draft-list">{drafts.map((draft, index) => {
      const isDraft = draft.entry_kind === "draft";
      const href = isDraft ? `/dashboard/drafts/${String(draft.id)}` : `/dashboard/stories/${encodeURIComponent(String(draft.story_id || ""))}`;
      return <a className="table-row" href={href} key={`${String(draft.entry_kind)}-${String(draft.id || index)}`}><div><span className="tag tag-outline">{isDraft ? `v${String(draft.version || "—")}` : "Request"}</span> <span className={`status-chip status-${text(draft, "display_status", text(draft, "status")).toLowerCase().replaceAll(" ", "-")}`}>{text(draft, "display_status", text(draft, "status"))}</span></div><div className="table-main"><strong>{isDraft ? text(draft, "headline", text(draft, "story_headline", "Draft")) : text(draft, "story_headline", "Draft request")}</strong><small>{text(draft, "mode", "Content")} · {text(draft, "lane", "Unassigned")}{!isDraft ? ` · ${text(draft, "status_message", "Queued")}` : ""}</small></div><div className="table-date">{dateTime(draft.updated_at)}<span>→</span></div></a>;
    })}{!drafts.length ? <div className="empty-state"><strong>No content yet.</strong><span>Open an article in the news inbox to generate content or graphics.</span></div> : null}</div></section>
  </DashboardShell>;
}
