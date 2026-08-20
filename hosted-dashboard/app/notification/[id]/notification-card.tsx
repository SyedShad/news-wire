"use client";

import { useEffect, useState } from "react";
import {
  loadCachedPushPayload,
  storeCachedPushPayload,
  submitPushAction,
  type NotificationArticlePayload,
  type PushAction,
} from "@/lib/push/client.ts";

function displayedDate(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "Not provided";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? String(value) : date.toLocaleString();
}

function label(value: string): string {
  return value.replaceAll("_", " ");
}

export default function NotificationCard({ eventId }: { eventId: string }) {
  const [payload, setPayload] = useState<NotificationArticlePayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<PushAction | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    loadCachedPushPayload(eventId).then((nextPayload) => {
      if (active) {
        setPayload(nextPayload?.kind === "article_alert" ? nextPayload : null);
        setLoading(false);
      }
    }).catch(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [eventId]);

  async function act(action: PushAction) {
    if (!payload) return;
    setBusy(action);
    setMessage(null);
    setError(null);
    const submitting = { ...payload, actionState: { action, status: "submitting" as const } };
    setPayload(submitting);
    await storeCachedPushPayload(submitting);
    try {
      const result = await submitPushAction(payload, action);
      const resultStatus = typeof result.status === "string" ? result.status : "";
      const completedAction: PushAction = resultStatus.includes("dismiss") ? "dismiss" : "start_research";
      const completed = { ...payload, actionState: { action: completedAction, status: "completed" as const } };
      setPayload(completed);
      await storeCachedPushPayload(completed);
      setMessage(completedAction === "dismiss"
        ? "This notification is dismissed. The item remains available in the News Wire."
        : "Fresh source research has been queued. No content draft was created.");
    } catch (reason) {
      const detail = reason instanceof Error ? reason.message : "The action could not be completed.";
      const failed = { ...payload, actionState: { action, status: "failed" as const, message: detail } };
      setPayload(failed);
      await storeCachedPushPayload(failed);
      setError(detail);
    } finally {
      setBusy(null);
    }
  }

  const completedAction = payload?.actionState?.status === "completed" ? payload.actionState.action : null;
  return (
    <main className="notification-page">
      <article className="notification-card" aria-busy={loading}>
        <header className="notification-brand">
          <span className="brand-mark wire-notification-mark" aria-hidden="true"><span /><span /><span /></span>
          <div><strong>Open Source AI</strong><small>News Wire notification</small></div>
        </header>
        {payload ? (
          <>
            <div className="notification-labels">
              <span>{payload.category}</span>
              {payload.sourceType ? <span>{label(payload.sourceType)}</span> : null}
            </div>
            <h1>{payload.title}</h1>
            <p className="notification-publisher">{payload.publisher}</p>
            <p className="notification-context">{payload.context}</p>
            <dl className="notification-details">
              <div><dt>Published</dt><dd>{displayedDate(payload.publishedAt)}</dd></div>
              <div><dt>Detected</dt><dd>{displayedDate(payload.detectedAt)}</dd></div>
              <div><dt>Context source</dt><dd>{label(payload.provenance)}</dd></div>
            </dl>
          </>
        ) : (
          <div className="notification-unavailable" role="status">
            <h1>{loading ? "Loading notification…" : "Notification details unavailable"}</h1>
            <p>{loading ? "Reading the notification saved on this device." : "Open this card by tapping the original browser notification on the subscribed device."}</p>
          </div>
        )}
        <div className="notification-action-row" aria-label="News item actions">
          <button type="button" className="button button-push-primary" disabled={!payload || busy !== null || completedAction !== null} onClick={() => void act("start_research")}>{busy === "start_research" ? "Starting…" : "Start working"}</button>
          <button type="button" className="button" disabled={!payload || busy !== null || completedAction !== null} onClick={() => void act("dismiss")}>{busy === "dismiss" ? "Dismissing…" : "Dismiss"}</button>
        </div>
        <div className="notification-feedback" aria-live="polite">
          {message ? <p className="notification-success">{message}</p> : null}
          {error ? <p className="notification-error">{error}</p> : null}
        </div>
      </article>
      <p className="notification-privacy-note">The full title and context shown here may also appear in lock-screen previews.</p>
    </main>
  );
}
