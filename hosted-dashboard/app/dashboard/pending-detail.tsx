"use client";

import { useEffect } from "react";

export default function PendingDetail({ label }: { label: string }) {
  useEffect(() => {
    const timer = window.setTimeout(() => window.location.reload(), 3_000);
    return () => window.clearTimeout(timer);
  }, []);
  return <section className="data-panel"><span className="status-pill status-pill-waiting">Fetching from laptop</span><h2>{label}</h2><p className="empty-state">This page will refresh automatically when the secure bridge returns the requested detail.</p></section>;
}
