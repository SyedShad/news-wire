import type { NotificationArticleProjection } from "../dashboard/types.ts";

export type PushSubscriptionInput = {
  endpoint: string;
  expirationTime: number | null;
  keys: { p256dh: string; auth: string };
};

export type StoredPushSubscription = {
  id: string;
  endpoint: string;
  expirationTime: number | null;
  keys: { p256dh: string; auth: string };
};

export type PushAction = "start_research" | "dismiss";
export type PushRuntimeMode = "shadow" | "active" | "paused";

export type ArticleAlertPayload = {
  schemaVersion: 1;
  kind: "article_alert";
  eventId: string;
  storyId: string;
  title: string;
  publisher: string;
  category: string;
  context: string;
  sourceType: string;
  publishedAt: string | null;
  detectedAt: string;
  provenance: string;
  actions?: {
    startResearch: { capability: string };
    dismiss: { capability: string };
  };
};

export type InformationalPushPayload = {
  schemaVersion: 1;
  kind: "research_result" | "canary";
  eventId: string;
  storyId: string;
  title: string;
  publisher: string;
  category: string;
  context: string;
  publishedAt: string | null;
  detectedAt: string;
  provenance: string;
};

export type PushPayload = ArticleAlertPayload | InformationalPushPayload;

export function articleProjectionToPayload(article: NotificationArticleProjection): ArticleAlertPayload {
  return {
    schemaVersion: 1,
    kind: "article_alert",
    eventId: article.event_id,
    storyId: article.story_id,
    title: article.title,
    publisher: article.publisher,
    category: article.category,
    context: article.context,
    sourceType: article.source_type,
    publishedAt: article.published_at,
    detectedAt: article.detected_at,
    provenance: article.provenance,
  };
}
