import { requirePageSession } from "@/lib/auth/session.ts";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { listStoryPage } from "@/lib/dashboard/store.ts";
import DashboardShell from "../dashboard-shell.tsx";
import StoryCard from "../story-card.tsx";
export const dynamic = "force-dynamic";
type Query = Record<string, string | string[] | undefined>;
export default async function NewsInbox({ searchParams }: { searchParams: Promise<Query> }) {
  await requirePageSession(["master"]);
  const query = await searchParams;
  const cursor = typeof query.cursor === "string" ? query.cursor : "";
  const page = await listStoryPage(runtimeEnv().DB, { window: "all", sort: "newest", cursor, pageSize: 25 });
  return <DashboardShell active="inbox" eyebrow="Fetched articles" title="News inbox" intro="Every relevant article fetched from your sources is here. Open its source and decide what to create.">
    <section className="panel inbox-panel"><div className="panel-heading"><h2>Latest fetched articles</h2><span>{page.total.toLocaleString()} articles · newest fetched first</span></div><div className="story-list spacious">{page.stories.map((article) => <StoryCard story={article} key={article.id} />)}{!page.stories.length ? <div className="empty-state"><strong>No fetched articles yet.</strong><span>The next collection run will add articles here.</span></div> : null}</div></section>
    <nav className="pagination" aria-label="News inbox pagination">{cursor ? <a className="button button-secondary" href="/dashboard/inbox">Back to latest</a> : null}{page.nextCursor ? <a className="button button-secondary" href={`/dashboard/inbox?cursor=${encodeURIComponent(page.nextCursor)}`}>Next 25 →</a> : null}</nav>
  </DashboardShell>;
}
