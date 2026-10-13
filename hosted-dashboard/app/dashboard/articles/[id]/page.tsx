import { requirePageSession } from "@/lib/auth/session.ts";
import { redirect } from "next/navigation";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { listGenerationJobs, readDetail, readSnapshot, requestDetail } from "@/lib/dashboard/store.ts";
import DashboardShell from "../../dashboard-shell.tsx";
import PendingDetail from "../../pending-detail.tsx";
import GenerationWorkspace from "../../generation-workspace.tsx";
import { fullDateTime, records, safeExternalUrl, text } from "../../presentation.ts";
import { bridgeSupportsGeneration, generationEnabled } from "@/lib/dashboard/features.ts";
export const dynamic = "force-dynamic";
export default async function ArticleDetail({ params }: { params: Promise<{ id: string }> }) {
  const session = await requirePageSession(["master"]);
  const { id } = await params;
  if (id.startsWith("story-")) redirect(`/dashboard/stories/${encodeURIComponent(id)}`);
  const source = runtimeEnv();
  const [stored, article, catalog, jobs] = await Promise.all([readSnapshot(source.DB), readDetail(source.DB, "article", id), readDetail(source.DB, "generation_catalog", "runtime"), listGenerationJobs(source.DB, id)]);
  const supportsGeneration = bridgeSupportsGeneration(stored?.bridgeVersion);
  if (!article && stored?.bridgeConnected && supportsGeneration) await requestDetail(source.DB, {resourceType: "article", resourceId: id, requestedBy: session.actorId});
  if (!catalog && stored?.bridgeConnected && supportsGeneration) await requestDetail(source.DB, {resourceType: "generation_catalog", resourceId: "runtime", requestedBy: session.actorId});
  if (!article) return <DashboardShell active="inbox" eyebrow="Article" title="Fetching article" intro="Retrieving the article from your laptop.">{stored?.bridgeConnected && supportsGeneration ? <PendingDetail label="Retrieving article details" /> : <section className="panel"><p>{supportsGeneration ? "Article details are unavailable while the laptop is offline." : "Article details need the upgraded laptop runtime. Your cached news remains available in the news inbox."}</p><a href="/dashboard/inbox">Open cached news</a></section>}</DashboardShell>;
  const url = safeExternalUrl(text(article, "canonical_url", text(article, "url", "")));
  const drafts = records(article, "drafts");
  return <DashboardShell active="inbox" eyebrow="Article workspace" title="" intro="" customHeader>
    <nav className="breadcrumb" aria-label="Breadcrumb"><a href="/dashboard/inbox">News inbox</a><span>›</span><span>Article workspace</span></nav>
    <header className="story-hero"><div className="story-hero-copy"><div className="eyebrow-row"><span className="tag tag-outline">Fetched</span><span className="eyebrow">{text(article, "publisher", text(article, "source_name", "Publisher"))}</span></div><h1>{text(article, "headline", text(article, "title", "Article"))}</h1><p>{text(article, "summary", "")}</p><div className="story-meta detailed"><span>Published <strong>{fullDateTime(article.published_at)}</strong></span><span>Fetched <strong>{fullDateTime(article.first_fetched_at || article.detected_at)}</strong></span></div>{url ? <a className="button button-secondary" href={url} target="_blank" rel="noreferrer noopener">Visit original source ↗</a> : null}</div></header>
    <GenerationWorkspace articleId={id} initialCatalog={catalog} initialJobs={jobs} initialConnected={Boolean(stored?.bridgeConnected)} initialEnabled={generationEnabled(source)} initialSupportsGeneration={supportsGeneration} />
    {records(article, "related_sources").length ? <details className="panel"><summary>Related source links</summary><div className="source-list">{records(article, "related_sources").map((item, index) => {const href = safeExternalUrl(text(item, "canonical_url", text(item, "url", "")));return href ? <a href={href} key={String(item.id || index)} target="_blank" rel="noreferrer noopener">{text(item, "title", text(item, "source_name", "Source"))} ↗</a> : null;})}</div></details> : null}
    {drafts.length ? <section className="panel"><h2>Earlier content</h2><div className="version-list">{drafts.map((draft) => <a key={String(draft.id)} href={`/dashboard/drafts/${String(draft.id)}`}>v{String(draft.version)} · {text(draft, "headline", "Draft")}</a>)}</div></section> : null}
  </DashboardShell>;
}
