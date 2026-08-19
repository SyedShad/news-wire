import type { ReactNode } from "react";
import { requirePageSession } from "@/lib/auth/session.ts";
import { accessProfile } from "@/lib/auth/authorization.ts";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { readSnapshot } from "@/lib/dashboard/store.ts";
import { dateTime, text } from "./presentation.ts";

const NAVIGATION = [
  ["overview", "/dashboard", "⌂", "Overview"],
  ["inbox", "/dashboard/inbox", "≡", "Review inbox"],
  ["drafts", "/dashboard/drafts", "✎", "Content"],
  ["sources", "/dashboard/sources", "◉", "Sources & health"],
  ["schedule", "/dashboard/schedule", "◷", "Schedule & usage"],
  ["settings", "/dashboard/settings", "⌁", "Settings & data"],
] as const;

export default async function DashboardShell(props: {
  active: string;
  eyebrow: string;
  title: string;
  intro: string;
  children: ReactNode;
  actions?: ReactNode;
  customHeader?: boolean;
}) {
  const session = await requirePageSession(["master"]);
  const profile = accessProfile(session.role);
  const stored = await readSnapshot(runtimeEnv().DB);
  const schedule = stored?.snapshot.schedule || {};
  const notices = stored?.snapshot.notices || [];
  const assistance = schedule.assistance && typeof schedule.assistance === "object" && !Array.isArray(schedule.assistance)
    ? schedule.assistance
    : undefined;
  const connected = Boolean(stored?.bridgeConnected);
  return (
    <>
      <a className="skip-link" href="#main-content">Skip to content</a>
      <div className="dashboard-app app-shell">
        <aside className="sidebar" aria-label="Primary navigation">
          <a className="brand" href="/dashboard" aria-label="Open Source AI News Wire overview">
            <span className="brand-mark wire-brand-mark" aria-hidden="true"><span /><span /><span /></span>
            <span className="brand-copy"><strong>Open Source AI</strong><small>News Wire</small></span>
          </a>
          <nav className="nav-list" aria-label="Dashboard sections">
            {NAVIGATION.map(([key, href, icon, label]) => (
              <a key={key} className={`nav-item ${props.active === key ? "active" : ""}`} href={href} aria-current={props.active === key ? "page" : undefined}>
                <span className="nav-icon" aria-hidden="true">{icon}</span><span>{label}</span>
                {key === "inbox" && notices.length ? <span className="nav-count">{notices.length}</span> : null}
              </a>
            ))}
          </nav>
          <div className="sidebar-foot">
            <div className="local-state">
              <span className={`pulse-dot ${connected ? "" : "is-paused"}`} />
              <span><strong>Remote session</strong><small>{connected ? "Live" : "Cached"}</small></span>
            </div>
            <form method="post" action="/api/auth/logout"><button className="stop-button" type="submit">Sign out</button></form>
            <small className="owner-label">{profile.label} · Full operational access</small>
          </div>
        </aside>
        <main className="main" id="main-content">
          <div className={`demo-banner ${connected ? "sync-live" : "sync-cached"}`} role="status">
            <span className="demo-pill">{connected ? "Live bridge" : "Cached"}</span>
            {connected ? "Laptop data is current." : "The laptop is offline. Read-only cached data is shown and actions are disabled."}
            <small>Last sync {dateTime(stored?.receivedAt)}</small>
          </div>
          {assistance && assistance.ready !== true ? (
            <div className="demo-banner assistance-banner" role="status">
              <span className="demo-pill">Drafting unavailable</span>
              {text(assistance, "failure_reason", "The assistance security check has not passed.").replaceAll("_", " ")}
            </div>
          ) : null}
          {!props.customHeader ? (
            <header className="page-header">
              <div><p className="kicker">{props.eyebrow}</p><h1>{props.title}</h1><p className="lede">{props.intro}</p></div>
              {props.actions ? <div className="header-actions">{props.actions}</div> : null}
            </header>
          ) : null}
          {props.children}
        </main>
      </div>
    </>
  );
}
