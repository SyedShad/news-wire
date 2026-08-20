import { requirePageSession } from "@/lib/auth/session.ts";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { listCommands, readSnapshot } from "@/lib/dashboard/store.ts";
import DashboardShell from "../dashboard-shell.tsx";
import SettingsActions from "../settings-actions.tsx";
import PushSettings from "../push-settings.tsx";
import { dateTime, number, text, type RecordValue } from "../presentation.ts";

export const dynamic = "force-dynamic";

export default async function Settings() {
  await requirePageSession(["master"]);
  const source = runtimeEnv();
  const [stored, commands] = await Promise.all([readSnapshot(source.DB), listCommands(source.DB, 30)]);
  const settings = stored?.snapshot.settings || {};
  const diagnostics = stored?.snapshot.diagnostics || [];
  const counts = settings.counts && typeof settings.counts === "object" && !Array.isArray(settings.counts) ? settings.counts as RecordValue : undefined;
  return <DashboardShell active="settings" eyebrow="Local boundaries" title="Settings & data" intro="The runtime corpus stays on your Mac. The hosted dashboard contains only the protected, redacted projection needed for remote access.">
    <div className="settings-grid">
      <section className="panel setting-card span-two"><div className="panel-heading"><div><p className="section-index">01</p><h2>Runtime data</h2></div><span className="status-chip status-healthy">Validated</span></div><div className="path-box"><code>Laptop-local private data root</code><span>{stored?.bridgeConnected ? "Connected" : "Cached"}</span></div><dl className="detail-list horizontal"><div><dt>Database</dt><dd>{text(settings, "database_size")}</dd></div><div><dt>Mode</dt><dd>{settings.demo_mode === true ? "Fictional demo" : "Live local data"}</dd></div><div><dt>Version</dt><dd>{text(settings, "app_version", stored?.snapshot.runtime_version || "—")}</dd></div><div><dt>Bridge</dt><dd>{stored?.bridgeVersion || "—"} · {dateTime(stored?.bridgeLastSeenAt)}</dd></div></dl></section>
      <section className="panel setting-card danger-card"><div className="panel-heading compact"><div><p className="section-index">02</p><h2>No backup in V1</h2></div></div><p className="setting-copy">Disk loss or corruption may permanently remove collected evidence, decisions, and drafts. This product does not create synchronized corpus copies.</p><span className="risk-label">Accepted pilot risk</span></section>
      <section className="panel setting-card"><div className="panel-heading compact"><div><p className="section-index">03</p><h2>Stored records</h2></div></div><dl className="count-list">{counts ? Object.entries(counts).map(([label, value]) => <div key={label}><dt>{label.replaceAll("_", " ")}</dt><dd>{typeof value === "number" ? value.toLocaleString() : String(value)}</dd></div>) : <div><dt>Stories</dt><dd>{number(counts, "stories")}</dd></div>}</dl></section>
      <section className="panel setting-card span-two"><div className="panel-heading"><div><p className="section-index">04</p><h2>Local diagnostics</h2></div><a href="/api/dashboard/diagnostics/export">Export redacted JSON</a></div><div className="diagnostic-list">{diagnostics.map((item, index) => <div key={String(item.id || index)}><span className={`status-chip status-${text(item, "level", "info")}`}>{text(item, "level", "info")}</span><strong>{text(item, "event_type", "Event")}</strong><p>{text(item, "message", "")}</p><small>{dateTime(item.created_at)}</small></div>)}{!diagnostics.length ? <div className="empty-state"><span>No diagnostic events stored.</span></div> : null}</div></section>
      <section className="panel setting-card span-two"><div className="panel-heading"><div><p className="section-index">05</p><h2>Recent owner commands</h2></div><span>Relay activity</span></div><div className="command-list">{commands.map((command) => <div key={command.id}><strong>{command.operation}</strong><span className={`command-${command.status}`}>{command.status}</span><small>{command.error || `${new Date(command.createdAt).toLocaleString()} · expires ${new Date(command.expiresAt).toLocaleTimeString()}`}</small></div>)}{!commands.length ? <p className="empty-state">No commands have been queued.</p> : null}</div></section>
      <PushSettings />
    </div>
    <SettingsActions connected={Boolean(stored?.bridgeConnected)} />
  </DashboardShell>;
}
