import { requirePageSession } from "@/lib/auth/session.ts";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { listStoryPage, readSnapshot } from "@/lib/dashboard/store.ts";
import DashboardShell from "./dashboard-shell.tsx";
import StoryCard from "./story-card.tsx";
import OverviewActions from "./overview-actions.tsx";
import { dateTime, number, operationalNotices, text } from "./presentation.ts";
export const dynamic = "force-dynamic";
export default async function Dashboard() {
  await requirePageSession(["master"]);
  const [stored, articles] = await Promise.all([readSnapshot(runtimeEnv().DB), listStoryPage(runtimeEnv().DB, {window: "all", sort: "newest", pageSize: 5})]);
  const schedule = stored?.snapshot.schedule || {};
  const notices = operationalNotices(stored?.snapshot.notices || []).filter((notice) => !notice.read_at);
  const generated = number(stored?.snapshot.overview.counts, "generated");
  return <DashboardShell active="overview" eyebrow="Your news desk" title="What changed in AI?" intro="Fetched articles, your generation work and collection operations in one place." actions={<OverviewActions connected={Boolean(stored?.bridgeConnected)} />}>
    <section className="stat-grid" aria-label="Current workload"><article className="stat-card stat-accent"><span className="stat-label">Fetched articles</span><strong>{articles.total.toLocaleString()}</strong><small>Every relevant article retained</small></article><article className="stat-card"><span className="stat-label">Generated outputs</span><strong>{generated}</strong><small>Completed text and graphics work</small></article><article className="stat-card"><span className="stat-label">Operational notices</span><strong>{notices.length}</strong><small>Unread collection notices</small></article><article className="stat-card"><span className="stat-label">Collection</span><strong>{text(schedule, "status", "Paused")}</strong><small>Next pass {dateTime(schedule.next_scan_at)}</small></article></section>
    <section className="panel"><div className="panel-heading"><h2>Latest fetched articles</h2><a href="/dashboard/inbox">See all {articles.total.toLocaleString()} articles →</a></div><div className="story-list">{articles.stories.map((article) => <StoryCard story={article} key={article.id} />)}{!articles.stories.length ? <div className="empty-state"><strong>No fetched articles yet.</strong><span>Queue a collection run to fetch articles.</span></div> : null}</div></section>
    <section className="panel"><div className="panel-heading"><h2>Operational review</h2><a href="/dashboard/operations">Open all notices →</a></div><p>{notices.length ? `${notices.length} unread operational notices.` : "No unread operational notices."}</p></section>
  </DashboardShell>;
}
