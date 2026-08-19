import { requirePageSession } from "@/lib/auth/session.ts";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { readSnapshot } from "@/lib/dashboard/store.ts";
import DashboardShell from "../dashboard-shell.tsx";
import SourceToggle from "../source-toggle.tsx";
import { dateTime, number, text, type RecordValue } from "../presentation.ts";

export const dynamic = "force-dynamic";

export default async function Sources() {
  await requirePageSession(["master"]);
  const stored = await readSnapshot(runtimeEnv().DB);
  const sources = stored?.snapshot.sources || [];
  const grouped = new Map<string, RecordValue[]>();
  for (const source of sources) {
    const family = text(source, "family", "Other sources");
    grouped.set(family, [...(grouped.get(family) || []), source]);
  }
  const enabled = sources.filter((source) => source.enabled === true);
  const healthCount = (state: string) => sources.filter((source) => text(source, "health", text(source, "operational_status", "unknown")) === state).length;
  return <DashboardShell active="sources" eyebrow="Coverage, not completeness" title="Sources & health" intro="Aggregators discover leads. Event and reporting sources establish what can be verified." actions={<div className="header-note"><strong>{healthCount("healthy")}/{enabled.length}</strong><span> enabled sources healthy</span></div>}>
    <div className="source-summary"><div><span className="health-mark health-healthy" /><strong>{healthCount("healthy")}</strong><small>Healthy</small></div><div><span className="health-mark health-degraded" /><strong>{healthCount("degraded")}</strong><small>Degraded</small></div><div><span className="health-mark health-paused" /><strong>{healthCount("paused")}</strong><small>Paused</small></div><div><span className="health-mark health-disabled" /><strong>{sources.length - enabled.length}</strong><small>Disabled</small></div></div>
    <section className="source-families">{Array.from(grouped.entries()).map(([family, familySources], familyIndex) => <details className="panel source-family" open key={family}><summary><span><small>{String(familyIndex + 1).padStart(2, "0")}</small><strong>{family}</strong></span><span>{familySources.length} source{familySources.length === 1 ? "" : "s"} <b>⌄</b></span></summary><div className="source-table">{familySources.map((source, index) => {
      const state = text(source, "health", text(source, "operational_status", "unknown"));
      const sourceName = text(source, "name", "Unnamed source");
      return <article className="source-health-row" key={text(source, "id", String(index))}><div className="source-name"><span className={`health-mark health-${state}`} /><div><strong>{sourceName}</strong><small>{text(source, "detail", text(source, "validation_status", ""))}</small></div></div><div><span className="cell-label">Role</span><strong>{text(source, "monitoring_role", "Discovery")}</strong></div><div><span className="cell-label">Lag</span><strong>{number(source, "lag_minutes")} min</strong></div><div><span className="cell-label">Checked</span><strong>{dateTime(source.last_checked_at)}</strong></div><div className="source-controls"><span className={`status-chip status-${source.enabled === true ? state : "disabled"}`}>{text(source, "operational_status", state).replaceAll("-", " ")}</span><SourceToggle id={text(source, "id", "")} name={sourceName} enabled={source.enabled === true} connected={Boolean(stored?.bridgeConnected)} /></div><div className="source-operational"><div><span>Cursor</span><code>{text(source, "cursor", "Not established")}</code></div><div><span>Failure streak</span><strong>{number(source, "failure_streak")}</strong></div><div><span>Last success</span><strong>{dateTime(source.last_success_at)}</strong></div><div><span>Recovery</span><strong>{source.enabled !== true ? "Monitoring disabled · cursor retained" : number(source, "failure_streak") ? `Retry pending after ${number(source, "failure_streak")} failures` : state === "paused" ? "Recovery suspended pending review" : "No open recovery gap"}</strong></div></div></article>;
    })}</div></details>)}</section>
  </DashboardShell>;
}
