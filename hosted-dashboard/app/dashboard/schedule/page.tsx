import { requirePageSession } from "@/lib/auth/session.ts";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { readSnapshot } from "@/lib/dashboard/store.ts";
import DashboardShell from "../dashboard-shell.tsx";
import ScheduleActions from "../schedule-actions.tsx";
import { dateTime, number, records, text } from "../presentation.ts";

export const dynamic = "force-dynamic";

export default async function Schedule() {
  await requirePageSession(["master"]);
  const stored = await readSnapshot(runtimeEnv().DB);
  const schedule = stored?.snapshot.schedule || {};
  return <DashboardShell active="schedule" eyebrow="Automation" title="Schedule and usage" intro="Laptop scheduler state, scan history, work queue, assisted-drafting readiness, and 24-hour usage.">
    <section className="connection-strip"><span className={`status-pill ${stored?.bridgeConnected ? "status-pill-live" : "status-pill-waiting"}`}>{stored?.bridgeConnected ? "Live" : "Cached"}</span><span>Last synchronized {dateTime(stored?.receivedAt)}</span></section>
    <section className="schedule-grid schedule-grid-large"><div><dt>Status</dt><dd>{text(schedule, "status")}</dd></div><div><dt>Last scan</dt><dd>{dateTime(schedule.last_scan_at)}</dd></div><div><dt>Next scan</dt><dd>{dateTime(schedule.next_scan_at)}</dd></div><div><dt>Background units</dt><dd>{number(schedule, "background_units")} / {number(schedule, "background_limit")}</dd></div><div><dt>Draft units</dt><dd>{number(schedule, "draft_units")}</dd></div><div><dt>Reserve</dt><dd>{number(schedule, "reserve_limit")}</dd></div></section>
    <section className="split-panels"><div className="data-panel"><div className="section-heading"><div><p className="eyebrow">Collection</p><h2>Recent scans</h2></div></div><div className="record-list">{records(schedule, "recent_scans").map((scan, index) => <article key={String(scan.id || index)}><strong>{text(scan, "status", "Scan")}</strong><span>{text(scan, "trigger", "scheduled")} · {number(scan, "items_seen")} items</span><small>{dateTime(scan.started_at)}</small></article>)}</div></div><div className="data-panel"><div className="section-heading"><div><p className="eyebrow">Work</p><h2>Queue</h2></div></div><div className="record-list">{records(schedule, "queue").map((item, index) => <article key={String(item.id || index)}><strong>{text(item, "kind", "Work item")}</strong><span>{text(item, "status", "queued")}</span><small>{dateTime(item.created_at)}</small></article>)}{!records(schedule, "queue").length ? <p className="empty-state">The work queue is clear.</p> : null}</div></div></section>
    <section className="data-panel"><div className="section-heading"><div><p className="eyebrow">Assisted drafting</p><h2>Readiness</h2></div></div><pre className="diagnostic-json">{JSON.stringify(schedule.assistance || {}, null, 2)}</pre></section>
    <ScheduleActions schedule={schedule} connected={Boolean(stored?.bridgeConnected)} />
  </DashboardShell>;
}
