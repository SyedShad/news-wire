import { requirePageSession } from "@/lib/auth/session.ts";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { listCommands, readSnapshot } from "@/lib/dashboard/store.ts";
import DashboardShell from "./dashboard-shell.tsx";
import OwnerControls from "./owner-controls.tsx";
import { dateTime, number, text } from "./presentation.ts";

export const dynamic = "force-dynamic";

export default async function Dashboard() {
  await requirePageSession(["master"]);
  const source = runtimeEnv();
  const stored = await readSnapshot(source.DB);
  const commands = await listCommands(source.DB, 30);
  const snapshot = stored?.snapshot;
  const connected = Boolean(stored?.bridgeConnected);
  const counts = snapshot?.overview.counts || {};
  const sourceCounts = snapshot?.overview.sources || {};
  const stories = snapshot?.stories || [];

  return (
    <DashboardShell active="overview" eyebrow="Open Source AI News Wire" title="Operational overview" intro="Live monitoring and laptop-backed owner operations from any device.">
      <section className="connection-card" aria-labelledby="connection-heading">
        <div>
          <span className={`status-pill ${connected ? "status-pill-live" : "status-pill-waiting"}`}>{connected ? "Live connection" : stored ? "Cached · laptop offline" : "Awaiting first sync"}</span>
          <h2 id="connection-heading">{connected ? "Dashboard is synchronized" : "Fresh operations are unavailable"}</h2>
          <p>{stored ? `Latest synchronized state: ${dateTime(stored.receivedAt)}. Cached information remains readable while the laptop is offline.` : "Authentication is working, but the laptop has not delivered the first redacted projection."}</p>
        </div>
        <dl className="connection-meta">
          <div><dt>Bridge</dt><dd>{connected ? "Online" : "Offline"}</dd></div>
          <div><dt>Protocol</dt><dd>{stored?.bridgeVersion || "—"}</dd></div>
          <div><dt>Laptop runtime</dt><dd>{snapshot?.runtime_version || "—"}</dd></div>
          <div><dt>Last heartbeat</dt><dd>{dateTime(stored?.bridgeLastSeenAt || stored?.receivedAt)}</dd></div>
        </dl>
      </section>

      <section className="metric-grid" aria-label="Operational totals">
        <article><span>Ready</span><strong>{Number(counts.ready || 0).toLocaleString()}</strong></article>
        <article><span>Review now</span><strong>{Number(counts.review_now || 0).toLocaleString()}</strong></article>
        <article><span>Researching</span><strong>{Number(counts.researching || 0).toLocaleString()}</strong></article>
        <article><span>Content ready</span><strong>{Number(counts.content_ready || 0).toLocaleString()}</strong></article>
        <article><span>Enabled sources</span><strong>{Number(sourceCounts.total || 0).toLocaleString()}</strong></article>
        <article><span>Healthy sources</span><strong>{Number(sourceCounts.healthy || 0).toLocaleString()}</strong></article>
      </section>

      <section className="data-panel">
        <div className="section-heading"><div><p className="eyebrow">Priority</p><h2>Top stories</h2></div><a className="button button-small" href="/dashboard/inbox">Open review inbox</a></div>
        <div className="story-list">
          {(snapshot?.overview.top_stories || []).map((story, index) => (
            <article className="story-row" key={text(story, "id", String(index))}>
              <div className="story-rank">{String(index + 1).padStart(2, "0")}</div>
              <div><h3><a href={`/dashboard/stories/${encodeURIComponent(text(story, "id", ""))}`}>{text(story, "headline", "Untitled story")}</a></h3><p>{text(story, "summary", "No summary available.")}</p><div className="story-meta"><span>{text(story, "priority", "Standard")}</span><span>{text(story, "lane", "Unassigned")}</span><span>{text(story, "status", "unknown")}</span><span>{dateTime(story.detected_at)}</span></div></div>
            </article>
          ))}
          {!snapshot?.overview.top_stories.length ? <p className="empty-state">No synchronized review stories.</p> : null}
        </div>
      </section>

      <section className="split-panels">
        <div className="data-panel"><div className="section-heading"><div><p className="eyebrow">Notices</p><h2>Recent alerts</h2></div></div><div className="compact-list">{(snapshot?.overview.alerts || []).map((alert, index) => <article key={String(alert.id || index)}><div><strong>{text(alert, "title", text(alert, "kind", "Alert"))}</strong><span>{text(alert, "body", text(alert, "message", ""))}</span></div><time>{dateTime(alert.created_at)}</time></article>)}{!snapshot?.overview.alerts.length ? <p className="empty-state">No recent alerts.</p> : null}</div></div>
        <div className="data-panel"><div className="section-heading"><div><p className="eyebrow">Collection</p><h2>Recent scans</h2></div></div><div className="compact-list">{(snapshot?.overview.scans || []).map((scan, index) => <article key={String(scan.id || index)}><div><strong>{text(scan, "status", "Scan")}</strong><span>{text(scan, "trigger", "scheduled")} · {number(scan, "items_seen")} items</span></div><time>{dateTime(scan.started_at)}</time></article>)}{!snapshot?.overview.scans.length ? <p className="empty-state">No recent scans.</p> : null}</div></div>
      </section>

      <OwnerControls stories={stories} drafts={snapshot?.drafts || []} sources={snapshot?.sources || []} schedule={snapshot?.schedule || {}} initialCommands={commands} connected={connected} />
    </DashboardShell>
  );
}
