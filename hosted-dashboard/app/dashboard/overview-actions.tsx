"use client";

import { useOwnerCommand } from "./command-client.tsx";

export default function OverviewActions({ connected }: { connected: boolean }) {
  const { queue, busy } = useOwnerCommand();
  return (
    <>
      <a className="button button-secondary" href="/dashboard/inbox">Open review inbox</a>
      <button className="button button-primary" type="button" disabled={busy || !connected} onClick={() => queue("schedule.action", { action: "run_now" })}>Queue scout run</button>
    </>
  );
}
