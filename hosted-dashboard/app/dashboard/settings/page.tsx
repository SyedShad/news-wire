import { requirePageSession } from "@/lib/auth/session.ts";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { listCommands, readSnapshot } from "@/lib/dashboard/store.ts";
import DashboardShell from "../dashboard-shell.tsx";
import SettingsActions from "../settings-actions.tsx";
import { dateTime, number, text } from "../presentation.ts";

export const dynamic = "force-dynamic";

export default async function Settings() {
  await requirePageSession(["master"]);
  const source = runtimeEnv();
  const [stored, commands] = await Promise.all([readSnapshot(source.DB), listCommands(source.DB, 30)]);
  const settings = stored?.snapshot.settings || {};
  const diagnostics = stored?.snapshot.diagnostics || [];
  return <DashboardShell active="settings" eyebrow="Operations and recovery" title="Settings and data" intro="Runtime identity, retained data, bridge health, redacted diagnostics, command outcomes, exports, and purge controls.">
    <section className="connection-card"><div><span className={`status-pill ${stored?.bridgeConnected ? "status-pill-live" : "status-pill-waiting"}`}>{stored?.bridgeConnected ? "Bridge current" : "Bridge offline"}</span><h2>Hosted projection status</h2><p>The laptop remains canonical. This hosted copy contains only redacted dashboard projections and short-lived requested details.</p></div><dl className="connection-meta"><div><dt>Last heartbeat</dt><dd>{dateTime(stored?.bridgeLastSeenAt)}</dd></div><div><dt>Bridge version</dt><dd>{stored?.bridgeVersion || "—"}</dd></div><div><dt>Runtime version</dt><dd>{stored?.snapshot.runtime_version || "—"}</dd></div><div><dt>Last error</dt><dd>{stored?.bridgeLastError || "None"}</dd></div></dl></section>
    <section className="schedule-grid schedule-grid-large"><div><dt>Application</dt><dd>{text(settings, "app_version")}</dd></div><div><dt>Database size</dt><dd>{text(settings, "database_size")}</dd></div><div><dt>Stories</dt><dd>{number(settings.counts && typeof settings.counts === "object" && !Array.isArray(settings.counts) ? settings.counts : undefined, "stories")}</dd></div><div><dt>Drafts</dt><dd>{number(settings.counts && typeof settings.counts === "object" && !Array.isArray(settings.counts) ? settings.counts : undefined, "drafts")}</dd></div><div><dt>Operations</dt><dd>{number(settings.counts && typeof settings.counts === "object" && !Array.isArray(settings.counts) ? settings.counts : undefined, "operations")}</dd></div><div><dt>Mode</dt><dd>{settings.demo_mode === true ? "Demo" : "Live"}</dd></div></section>
    <section className="data-panel"><div className="section-heading"><div><p className="eyebrow">Redacted local events</p><h2>Diagnostics</h2></div><a className="button button-small" href="/api/dashboard/diagnostics/export">Export JSON</a></div><div className="record-list">{diagnostics.map((item, index) => <article key={String(item.id || index)}><strong>{text(item, "event_type", text(item, "level", "Event"))}</strong><span>{text(item, "message", "")}</span><small>{dateTime(item.created_at)}</small></article>)}{!diagnostics.length ? <p className="empty-state">No diagnostic events are retained.</p> : null}</div></section>
    <section className="data-panel"><div className="section-heading"><div><p className="eyebrow">Relay activity</p><h2>Recent owner commands</h2></div></div><div className="command-list">{commands.map((command) => <div key={command.id}><strong>{command.operation}</strong><span className={`command-${command.status}`}>{command.status}</span><small>{command.error || `${new Date(command.createdAt).toLocaleString()} · expires ${new Date(command.expiresAt).toLocaleTimeString()}`}</small></div>)}{!commands.length ? <p className="empty-state">No commands have been queued.</p> : null}</div></section>
    <SettingsActions connected={Boolean(stored?.bridgeConnected)} />
  </DashboardShell>;
}
