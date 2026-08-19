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
  const assistance = schedule.assistance && typeof schedule.assistance === "object" && !Array.isArray(schedule.assistance) ? schedule.assistance : {};
  return <DashboardShell active="schedule" eyebrow="Short-lived local work" title="Schedule & usage" intro="The scout runs at minute 00 and 30 while the Mac is awake. Sleep creates a catch-up gap; it never changes freshness.">
    <div className="schedule-grid">
      <section className="panel schedule-control-card"><div className="panel-heading"><div><p className="section-index">01</p><h2>Local scheduler</h2></div><span className={`status-chip status-${text(schedule, "status", "paused")}`}>{text(schedule, "status", "paused").replaceAll("_", " ")}</span></div><div className="clock-face"><span className="clock-hand hand-one" /><span className="clock-hand hand-two" /><strong>00</strong><small>30</small></div><dl className="detail-list schedule-details"><div><dt>Installed</dt><dd>{schedule.installed === true ? "Yes" : "No — controls are staged only"}</dd></div><div><dt>Last scan</dt><dd>{dateTime(schedule.last_scan_at)}</dd></div><div><dt>Next scan</dt><dd>{dateTime(schedule.next_scan_at)}</dd></div><div><dt>Catch-up</dt><dd>72 hours automatic · extended on request</dd></div></dl></section>
      <section className="panel usage-card"><div className="panel-heading"><div><p className="section-index">02</p><h2>ChatGPT allowance</h2></div><span>Rolling 24 hours</span></div><div className="usage-meter"><div><strong>{number(schedule, "background_units")}</strong><span>of {number(schedule, "background_limit")} units</span></div></div><div className="usage-key"><span><i className="dot dot-accent" />Routine background</span><span><i className="dot dot-muted" />{number(schedule, "reserve_limit")} urgent reserve</span></div><div className="usage-note"><strong>{number(schedule, "draft_units")} draft units</strong><p>Drafting is tracked separately and does not consume the background proxy.</p></div><div className="privacy-row"><span className={`status-chip ${assistance.ready === true && assistance.enabled === true ? "status-healthy" : "status-paused"}`}>{assistance.ready === true && assistance.enabled === true ? "Assistance ready" : assistance.ready === true ? "Assistance ready but off" : "Assistance blocked"}</span><small>{text(assistance, "failure_reason", "Release-bound isolation is current.").replaceAll("_", " ")} · {number(assistance, "pending_work")} pending task{number(assistance, "pending_work") === 1 ? "" : "s"}</small></div></section>
    </div>
    <ScheduleActions schedule={schedule} connected={Boolean(stored?.bridgeConnected)} />
    <section className="panel alert-panel run-history"><div className="panel-heading"><div><p className="section-index">04</p><h2>Run history</h2></div><span>{records(schedule, "queue").length} queued tasks</span></div><div className="run-table"><div className="run-row run-head"><span>Started</span><span>Trigger</span><span>Sources</span><span>Found</span><span>Result</span></div>{records(schedule, "recent_scans").map((scan, index) => <div className="run-row" key={String(scan.id || index)}><span>{dateTime(scan.started_at)}</span><span>{text(scan, "trigger_type", text(scan, "trigger", "scheduled")).replaceAll("_", " ")}</span><span>{number(scan, "source_success_count")} ok · {number(scan, "source_failure_count")} failed</span><span>{number(scan, "discovered_count")}</span><span className={`status-chip status-${text(scan, "result", text(scan, "status", "unknown"))}`}>{text(scan, "result", text(scan, "status", "unknown"))}</span></div>)}</div></section>
  </DashboardShell>;
}
