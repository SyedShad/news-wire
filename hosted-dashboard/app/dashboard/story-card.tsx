import type { RecordValue } from "./presentation.ts";
import { dateTime, safeExternalUrl, text } from "./presentation.ts";

export default function StoryCard({ story }: { story: RecordValue; rank?: number }) {
  const id = text(story, "id", "");
  const detailPath = `/dashboard/${id.startsWith("story-") ? "stories" : "articles"}/${encodeURIComponent(id)}`;
  const sourceUrl = safeExternalUrl(text(story, "canonical_url", text(story, "url", "")));
  return <article className="story-card article-card">
    <div className="story-card-body">
      <div className="eyebrow-row"><span className="tag tag-outline">Fetched</span><span className="eyebrow">{text(story, "publisher", text(story, "source_name", "Publisher"))}</span></div>
      <h3><a href={detailPath}>{text(story, "headline", text(story, "title", "Untitled article"))}</a></h3>
      <p>{text(story, "summary", "")}</p>
      <div className="story-meta"><span>Published {dateTime(story.published_at)}</span><span>Fetched {dateTime(story.first_fetched_at || story.detected_at)}</span>{sourceUrl ? <a href={sourceUrl} target="_blank" rel="noreferrer noopener">Visit source ↗</a> : null}</div>
    </div>
    <a className="arrow-link" href={detailPath} aria-label={`Open ${text(story, "headline", text(story, "title", "article"))}`}>→</a>
  </article>;
}
