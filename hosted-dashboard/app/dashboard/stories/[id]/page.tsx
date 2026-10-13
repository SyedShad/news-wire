import { requirePageSession } from "@/lib/auth/session.ts";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { readDetail, readSnapshot, requestDetail } from "@/lib/dashboard/store.ts";
import DashboardShell from "../../dashboard-shell.tsx";
import PendingDetail from "../../pending-detail.tsx";
import { records, safeExternalUrl, text } from "../../presentation.ts";
export const dynamic = "force-dynamic";
export default async function HistoricalStory({ params }: { params: Promise<{id: string}> }) {
  const session = await requirePageSession(["master"]);
  const {id} = await params;
  const source = runtimeEnv();
  const [stored, story] = await Promise.all([readSnapshot(source.DB), readDetail(source.DB, "story", id)]);
  if (!story && stored?.bridgeConnected) await requestDetail(source.DB, {resourceType: "story", resourceId: id, requestedBy: session.actorId});
  return <DashboardShell active="inbox" eyebrow="Historical story" title={story ? text(story, "headline", "Story history") : "Retrieving story history"} intro="Your cached news, source links and earlier drafts remain available during setup.">
    <div className="generation-panels">{["Generate content", "Generate graphics"].map((label) => <section className="panel generation-panel" key={label}><h2>{label}</h2><p>Generation setup is pending the upgraded article feed and local validation. Your cached sources remain available.</p><button className="button button-primary" disabled>{label}</button></section>)}</div>
    {!story ? stored?.bridgeConnected ? <PendingDetail label="Retrieving historical story" /> : <p>History is unavailable while the laptop is offline.</p> : <><section className="panel"><h2>Sources</h2>{records(story, "sources").map((item,index) => {const url=safeExternalUrl(text(item,"canonical_url",text(item,"url","")));return url ? <p key={String(item.id || index)}><a href={url} target="_blank" rel="noreferrer noopener">{text(item,"title",text(item,"source_name","Source"))} ↗</a></p> : null;})}</section><section className="panel"><h2>Earlier content</h2><div className="version-list">{records(story,"drafts").map((draft) => <a key={String(draft.id)} href={`/dashboard/drafts/${String(draft.id)}`}>v{String(draft.version)} · {text(draft,"headline","Draft")}</a>)}</div></section></>}
  </DashboardShell>;
}
