import { requirePageSession } from "@/lib/auth/session.ts";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { listStoryPage, readSnapshot } from "@/lib/dashboard/store.ts";
import DashboardShell from "../dashboard-shell.tsx";
import StoryCard from "../story-card.tsx";
import NoticeActions from "../notice-actions.tsx";
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
  const notices = stored?.snapshot.notices || [];
  return <DashboardShell active="inbox" eyebrow="Fast content queue" title="News inbox" intro="Trust, research, and provenance are visible. Every active story can create content." actions={<NoticeActions connected={Boolean(stored?.bridgeConnected)} />}>
    <form className="filter-bar" method="get">
      <label>Review window<select name="window" defaultValue={options.window}><option value="review_now">Review Now · 24 hours</option><option value="older">Older Context</option><option value="all">All History</option></select></label>
      <label>Queue type<select name="kind" defaultValue={options.kind}><option value="all">All news</option><option value="ready">Ready</option><option value="researching">Researching</option><option value="content_ready">Content ready</option><option value="correction">Corrections</option><option value="catch_up">Catch-up</option><option value="health">Health notices</option></select></label>
      <label>Status<select name="status" defaultValue={options.status}><option value="all">All states</option><option value="ready">Ready</option><option value="content_ready">Content ready</option><option value="archived">Archived</option><option value="withdrawn">Withdrawn</option></select></label>
      <label>Coverage lane<select name="lane" defaultValue={options.lane}><option value="all">All coverage lanes</option><option>Open Ecosystem News</option><option>AGI Development</option><option>Broader AI News</option></select></label>
      <label>Sort<select name="sort" defaultValue={options.sort}><option value="priority">Priority</option><option value="newest">Newest first</option></select></label>
      <button className="button button-dark">Apply filters</button>
      <span className="result-count">{(page.total + notices.length).toLocaleString()} results</span>
    </form>
    {notices.length ? <section className="panel inbox-notices"><div className="panel-heading"><div><p className="section-index">Notices</p><h2>Operational review</h2></div><span>{notices.length} items</span></div><div className="notice-grid">{notices.slice(0, 12).map((notice, index) => <article className={`notice notice-${text(notice, "severity", "watch")}`} key={String(notice.id || index)}><span className="notice-kind">{text(notice, "kind", "Notice").replaceAll("_", " ")}</span><h3>{text(notice, "title", "Notice")}</h3><p>{text(notice, "body", text(notice, "message", ""))}</p><small>{dateTime(notice.created_at)}</small>{notice.story_id ? <a href={`/dashboard/stories/${encodeURIComponent(String(notice.story_id))}`}>Open related story →</a> : null}</article>)}</div></section> : null}
    <section className="panel inbox-panel"><div className="story-list spacious">{page.stories.map((story, index) => <StoryCard story={story} rank={index + 1} key={story.id} />)}{!page.stories.length ? <div className="empty-state"><strong>No stories match these filters.</strong><span>Try a broader status or coverage lane.</span></div> : null}</div></section>
    <nav className="pagination" aria-label="Review queue pagination"><a className="button button-secondary" href="/dashboard/inbox">Reset</a>{page.nextCursor ? <a className="button button-secondary" href={`/dashboard/inbox?${next.toString()}`}>Next 25 →</a> : null}</nav>
  </DashboardShell>;
}
