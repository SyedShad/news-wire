import { requirePageSession } from "@/lib/auth/session.ts";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { listStoryPage, readSnapshot } from "@/lib/dashboard/store.ts";
import DashboardShell from "../dashboard-shell.tsx";
import { dateTime, text } from "../presentation.ts";

export const dynamic = "force-dynamic";

type Query = Record<string, string | string[] | undefined>;
const first = (value: string | string[] | undefined, fallback: string) => typeof value === "string" ? value : fallback;

export default async function ReviewInbox({ searchParams }: { searchParams: Promise<Query> }) {
  await requirePageSession(["master"]);
  const query = await searchParams;
  const options = {
    window: first(query.window, "review_now"), status: first(query.status, "all"), lane: first(query.lane, "all"),
    kind: first(query.kind, "all"), sort: first(query.sort, "priority"), cursor: first(query.cursor, ""), pageSize: 25,
  };
  const source = runtimeEnv();
  const [page, stored] = await Promise.all([listStoryPage(source.DB, options), readSnapshot(source.DB)]);
  const next = new URLSearchParams({ window: options.window, status: options.status, lane: options.lane, kind: options.kind, sort: options.sort });
  if (page.nextCursor) next.set("cursor", page.nextCursor);
  return <DashboardShell active="inbox" eyebrow="Review workflow" title="Review inbox" intro="Review Now, older context, full history, filters, sorting, notices, and cursor pagination.">
    <section className="connection-strip"><span className={`status-pill ${stored?.bridgeConnected ? "status-pill-live" : "status-pill-waiting"}`}>{stored?.bridgeConnected ? "Live" : "Cached"}</span><span>Last synchronized {dateTime(stored?.receivedAt)}</span><strong>{page.total.toLocaleString()} matching stories</strong></section>
    <form className="filter-bar" method="get">
      <label>Window<select name="window" defaultValue={options.window}><option value="review_now">Review Now</option><option value="older">Older Context</option><option value="all">All History</option></select></label>
      <label>Status<select name="status" defaultValue={options.status}><option value="all">All statuses</option><option value="ready">Ready</option><option value="candidate">Candidate</option><option value="watch">Watch</option><option value="researching">Researching</option><option value="content_ready">Content ready</option><option value="archived">Archived</option><option value="withdrawn">Withdrawn</option></select></label>
      <label>Lane<select name="lane" defaultValue={options.lane}><option value="all">All lanes</option><option>Models & Research</option><option>Developer Tools</option><option>Infrastructure</option><option>Policy & Governance</option></select></label>
      <label>Kind<select name="kind" defaultValue={options.kind}><option value="all">All kinds</option><option value="ready">Ready</option><option value="researching">Researching</option><option value="content_ready">Content ready</option><option value="catch_up">Catch-up</option><option value="correction">Corrections</option></select></label>
      <label>Sort<select name="sort" defaultValue={options.sort}><option value="priority">Priority</option><option value="newest">Newest</option></select></label>
      <button className="button button-small">Apply</button>
    </form>
    {(stored?.snapshot.notices || []).length ? <section className="notice-list">{(stored?.snapshot.notices || []).slice(0, 12).map((notice, index) => <article key={String(notice.id || index)}><strong>{text(notice, "title", text(notice, "kind", "Notice"))}</strong><span>{text(notice, "body", text(notice, "message", ""))}</span><time>{dateTime(notice.created_at)}</time></article>)}</section> : null}
    <section className="data-panel"><div className="story-list">{page.stories.map((story, index) => <article className="story-row" key={story.id}><div className="story-rank">{String(index + 1).padStart(2, "0")}</div><div><h3><a href={`/dashboard/stories/${encodeURIComponent(story.id)}`}>{text(story, "headline", "Untitled story")}</a></h3><p>{text(story, "summary", "No summary available.")}</p><div className="story-meta"><span>{text(story, "priority", "Standard")}</span><span>{text(story, "lane", "Unassigned")}</span><span>{text(story, "status", "unknown")}</span><span>{text(story, "freshness", "—")}</span><span>{dateTime(story.detected_at)}</span></div></div></article>)}{!page.stories.length ? <p className="empty-state">No stories match these filters.</p> : null}</div></section>
    <nav className="pagination" aria-label="Story pages"><a className="button button-small" href="/dashboard/inbox">Reset</a>{page.nextCursor ? <a className="button button-small" href={`/dashboard/inbox?${next.toString()}`}>Next page</a> : <span>End of results</span>}</nav>
  </DashboardShell>;
}
