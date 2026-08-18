import { requirePageSession } from "@/lib/auth/session.ts";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { readSnapshot } from "@/lib/dashboard/store.ts";
import DashboardShell from "../dashboard-shell.tsx";
import SourceActions from "../source-actions.tsx";
import { dateTime, text } from "../presentation.ts";

export const dynamic = "force-dynamic";

export default async function Sources() {
  await requirePageSession(["master"]);
  const stored = await readSnapshot(runtimeEnv().DB);
  const sources = stored?.snapshot.sources || [];
  return <DashboardShell active="sources" eyebrow="Collection coverage" title="Sources and health" intro="Registry state, source family, validation, operational health, collection timestamps, and enablement.">
    <section className="connection-strip"><span className={`status-pill ${stored?.bridgeConnected ? "status-pill-live" : "status-pill-waiting"}`}>{stored?.bridgeConnected ? "Live" : "Cached"}</span><span>Last synchronized {dateTime(stored?.receivedAt)}</span><strong>{sources.length} sources</strong></section>
    <section className="data-panel"><div className="source-table source-table-wide">{sources.map((source, index) => <article key={text(source, "id", String(index))}><div><strong>{text(source, "name", "Unnamed source")}</strong><span>{text(source, "family", "Unassigned")} · {text(source, "validation_status", "unknown")}</span></div><span className={`health health-${text(source, "operational_status", text(source, "health", "unknown"))}`}>{text(source, "operational_status", text(source, "health"))}</span><time>{dateTime(source.last_success_at || source.updated_at)}</time></article>)}</div></section>
    <section className="owner-control-card"><div className="section-heading"><div><p className="eyebrow">Owner operations</p><h2>Source enablement</h2></div></div>{!stored?.bridgeConnected ? <p className="owner-warning">Source changes are disabled while the laptop is offline.</p> : null}<SourceActions sources={sources} connected={Boolean(stored?.bridgeConnected)} /></section>
  </DashboardShell>;
}
