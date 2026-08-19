import type { RecordValue } from "./presentation.ts";
import { dateTime, number, text } from "./presentation.ts";

function slug(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/gu, "-").replace(/^-|-$/gu, "");
}

export default function StoryCard({
  story,
  rank,
}: {
  story: RecordValue;
  rank: number;
}) {
  const id = text(story, "id", "");
  const priority = text(story, "priority", "Standard");
  const freshness = text(story, "freshness", "Older");
  const origins = number(story, "confirmed_origin_count");
  const currentUpdate = story.is_material_update_current === true;
  const recordedUpdate = story.material_update === true;

  return (
    <article className={`story-card priority-${slug(priority)}`}>
      <div className="story-rank">
        <span className="rank-number">{String(rank).padStart(2, "0")}</span>
        <span className="score" title="Current review score">{number(story, "review_score")}</span>
      </div>
      <div className="story-card-body">
        <div className="eyebrow-row">
          <span className={`tag tag-${slug(freshness)}`}>{freshness}</span>
          <span className="eyebrow">{text(story, "lane", "Unassigned")}</span>
          {currentUpdate ? <span className="material-flag">Updated {text(story, "ranking_age_label", "recently")} · First published {text(story, "age_label", "—")}</span> : null}
          {!currentUpdate && recordedUpdate ? <span className="material-flag">Material update recorded</span> : null}
        </div>
        <h3><a href={`/dashboard/stories/${encodeURIComponent(id)}`}>{text(story, "headline", "Untitled story")}</a></h3>
        <p>{text(story, "summary", "No summary available.")}</p>
        <div className="story-meta">
          <span>{priority}</span>
          <span>First public {dateTime(story.first_public_at)}</span>
          {!currentUpdate ? <span>{text(story, "age_label", "—")}</span> : null}
          <span>{text(story, "impact_level", "Standard impact")}</span>
          <span>{text(story, "attention_level", "Low attention")}</span>
          <span>{text(story, "source_trust_state", "") === "trusted" ? "Trusted source" : "Research required"}</span>
          <span>{text(story, "research_status", "Unavailable").replaceAll("_", " ")} · {origins} source origin{origins === 1 ? "" : "s"}</span>
        </div>
      </div>
      <a className="arrow-link" href={`/dashboard/stories/${encodeURIComponent(id)}`} aria-label={`Open ${text(story, "headline", "story")}`}>→</a>
    </article>
  );
}
