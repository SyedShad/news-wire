import type { ReactNode } from "react";
import { requirePageSession } from "@/lib/auth/session.ts";
import { accessProfile } from "@/lib/auth/authorization.ts";

const NAVIGATION = [
  ["overview", "/dashboard", "Overview"],
  ["inbox", "/dashboard/inbox", "Review inbox"],
  ["drafts", "/dashboard/drafts", "Content & history"],
  ["sources", "/dashboard/sources", "Sources & health"],
  ["schedule", "/dashboard/schedule", "Schedule & usage"],
  ["settings", "/dashboard/settings", "Settings & data"],
] as const;

export default async function DashboardShell(props: {
  active: string;
  eyebrow: string;
  title: string;
  intro: string;
  children: ReactNode;
}) {
  const session = await requirePageSession(["master"]);
  const profile = accessProfile(session.role);
  return (
    <div className="dashboard-app">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <span className="brand-mark brand-mark-small" aria-hidden="true">S</span>
          <div><strong>News Wire</strong><span>Laptop-backed dashboard</span></div>
        </div>
        <nav aria-label="Dashboard sections">
          {NAVIGATION.map(([key, href, label]) => (
            <a key={key} className={`nav-item ${props.active === key ? "nav-item-active" : ""}`} href={href}>{label}</a>
          ))}
        </nav>
        <div className="sidebar-access">
          <span className="access-dot access-dot-full_control" aria-hidden="true" />
          <div><strong>{profile.label}</strong><span>Password-authenticated owner</span></div>
        </div>
      </aside>
      <main className="dashboard-main">
        <header className="dashboard-header">
          <div>
            <p className="eyebrow">{props.eyebrow}</p>
            <h1>{props.title}</h1>
            <p className="dashboard-intro">{props.intro}</p>
          </div>
          <div className="header-actions">
            <span className="role-pill role-pill-full_control">{profile.label}</span>
            <form method="post" action="/api/auth/logout"><button className="button button-small" type="submit">Sign out</button></form>
          </div>
        </header>
        {props.children}
      </main>
    </div>
  );
}
