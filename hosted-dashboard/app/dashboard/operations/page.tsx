import { requirePageSession } from "@/lib/auth/session.ts";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { readSnapshot } from "@/lib/dashboard/store.ts";
import DashboardShell from "../dashboard-shell.tsx";
import NoticeActions from "../notice-actions.tsx";
import { dateTime, operationalNotices, operationalUnreadCount, text } from "../presentation.ts";
import { bridgeSupportsGeneration } from "@/lib/dashboard/features.ts";
export const dynamic = "force-dynamic";
export default async function Operations() {
  await requirePageSession(["master"]);
  const stored = await readSnapshot(runtimeEnv().DB);
  const notices = operationalNotices(stored?.snapshot.notices || []);
  const unread = operationalUnreadCount(stored?.snapshot.overview, notices);
  return <DashboardShell active="operations" eyebrow="Collection and runtime notices" title="Operational review" intro="Source health, corrections, recovery and collection notices. These notices do not restrict the news inbox." actions={<NoticeActions connected={Boolean(stored?.bridgeConnected)} legacyBridge={!bridgeSupportsGeneration(stored?.bridgeVersion)} />}>
    <section className="panel"><div className="panel-heading"><h2>Operational notices</h2><span>{unread} unread · {notices.length} total</span></div><div className="notice-grid">{notices.map((notice, index) => <article className={`notice notice-${text(notice, "severity", "watch")}`} key={String(notice.id || index)}><span className="notice-kind">{text(notice, "kind").replaceAll("_", " ")} · {notice.read_at ? "Read" : "Unread"}</span><h3>{text(notice, "title", "Notice")}</h3><p>{text(notice, "body", text(notice, "message", ""))}</p><small>{dateTime(notice.created_at)}</small></article>)}</div>{!notices.length ? <div className="empty-state"><strong>No operational notices.</strong><span>New notices appear here automatically.</span></div> : null}</section>
  </DashboardShell>;
}
