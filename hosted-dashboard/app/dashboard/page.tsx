import { requirePageSession } from "@/lib/auth/session.ts";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { readSnapshot } from "@/lib/dashboard/store.ts";
import DashboardShell from "./dashboard-shell.tsx";
import StoryCard from "./story-card.tsx";
import OverviewActions from "./overview-actions.tsx";
import { dateTime, text } from "./presentation.ts";

export const dynamic = "force-dynamic";

export default async function Dashboard() {
  await requirePageSession(["master"]);
  const source = runtimeEnv();
  const stored = await readSnapshot(source.DB);
  const snapshot = stored?.snapshot;
  const connected = Boolean(stored?.bridgeConnected);
  const counts = snapshot?.overview.counts || {};
  const sourceCounts = snapshot?.overview.sources || {};
  const schedule = snapshot?.schedule || {};
  const topStories = snapshot?.overview.top_stories || [];

  return (
    <DashboardShell active="overview" eyebrow="Local intelligence desk" title="What changed in AI?" intro="Stories move straight from discovery to research and content creation, with trust and source provenance always visible." actions={<OverviewActions connected={connected} />}>
      <section className="stat-grid" aria-label="Current workload">
        <article className="stat-card stat-accent"><span className="stat-label">Urgent</span><strong>{Number(counts.urgent || 0).toLocaleString()}</strong><small>High impact + immediate relevance</small></article>
        <article className="stat-card"><span className="stat-label">Ready</span><strong>{Number(counts.ready || 0).toLocaleString()}</strong><small>Content can be started now</small></article>
        <article className="stat-card"><span className="stat-label">Researching</span><strong>{Number(counts.researching || 0).toLocaleString()}</strong><small>Automatic source research active</small></article>
        <article className="stat-card"><span className="stat-label">Content ready</span><strong>{Number(counts.content_ready || 0).toLocaleString()}</strong><small>Editable and versioned</small></article>
      </section>
      <div className="dashboard-grid">
        <section className="panel priority-panel"><div className="panel-heading"><div><p className="section-index">01</p><h2>Priority wire</h2></div><a href="/dashboard/inbox">See all {Number(counts.review_now || 0).toLocaleString()} current items</a></div><div className="story-list">{topStories.map((story, index) => <StoryCard story={story} rank={index + 1} key={text(story, "id", String(index))} />)}{!topStories.length ? <div className="empty-state"><strong>No current news items.</strong><span>The next scout will add new stories here.</span></div> : null}</div></section>
        <aside className="dashboard-rail">
          <section className="panel health-card"><div className="panel-heading compact"><div><p className="section-index">02</p><h2>Coverage health</h2></div><a href="/dashboard/sources">Details</a></div><div className="health-orbit" aria-label={`${Number(sourceCounts.healthy || 0)} of ${Number(sourceCounts.total || 0)} enabled sources healthy`}><div className="orbit-value"><strong>{Number(sourceCounts.healthy || 0)}</strong><span>of {Number(sourceCounts.total || 0)}</span></div></div><div className="health-legend"><span><i className="dot dot-good" />{Number(sourceCounts.healthy || 0)} healthy</span><span><i className="dot dot-warn" />{Number(sourceCounts.degraded || 0)} degraded</span><span><i className="dot dot-muted" />{Number(sourceCounts.paused || 0)} paused</span></div></section>
          <section className="panel schedule-card"><div className="panel-heading compact"><div><p className="section-index">03</p><h2>Next pass</h2></div><a href="/dashboard/schedule">Manage</a></div><p className="big-time">{dateTime(schedule.next_scan_at)}</p><div className="schedule-line"><span className={`status-chip status-${text(schedule, "status", "paused")}`}>{text(schedule, "status", "paused").replaceAll("_", " ")}</span><span>00 + 30 each hour · shadow {schedule.shadow_mode === true ? "on" : "off"}</span></div><dl className="mini-stats"><div><dt>Queue</dt><dd>{Number(snapshot?.overview.queue_count || 0)}</dd></div><div><dt>Queue lag</dt><dd>{snapshot?.overview.queue_lag || "—"}</dd></div><div><dt>Last scan</dt><dd>{dateTime(schedule.last_scan_at)}</dd></div><div><dt>Background</dt><dd>{Number(schedule.background_units || 0)}/{Number(schedule.background_limit || 0)}</dd></div></dl></section>
        </aside>
      </div>
      <section className="panel alert-panel"><div className="panel-heading"><div><p className="section-index">04</p><h2>Latest notices</h2></div></div><div className="notice-grid">{(snapshot?.overview.alerts || []).slice(0, 4).map((alert, index) => <article className={`notice notice-${text(alert, "severity", "watch")}`} key={String(alert.id || index)}><span className="notice-kind">{text(alert, "kind", "Notice")}</span><h3>{text(alert, "title", "Notice")}</h3><p>{text(alert, "body", text(alert, "message", ""))}</p><small>{dateTime(alert.created_at)}</small></article>)}</div></section>
    </DashboardShell>
  );
}
