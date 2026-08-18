"use client";

import { CommandMessage, useOwnerCommand } from "./command-client.tsx";
import { text, type RecordValue } from "./presentation.ts";

export default function ScheduleActions({ schedule, connected }: { schedule: RecordValue; connected: boolean }) {
  const { queue, message, busy } = useOwnerCommand();
  const disabled = busy || !connected;
  return <section className="owner-control-card"><div className="section-heading"><div><p className="eyebrow">Owner operations</p><h2>Scheduler controls</h2></div></div>{!connected ? <p className="owner-warning">Controls are disabled while the laptop is offline.</p> : null}<CommandMessage message={message} /><div className="button-row control-actions">{["run_now", "pause", "resume", "install"].map((action) => <button className="button button-small" key={action} disabled={disabled} onClick={() => queue("schedule.action", { action })}>{action.replaceAll("_", " ")}</button>)}<button className="button button-small button-danger" disabled={disabled} onClick={() => queue("schedule.action", { action: "uninstall" }, "Uninstall the laptop scheduler?")}>uninstall</button></div><form className="inline-form" onSubmit={(event) => { event.preventDefault(); const data = new FormData(event.currentTarget); void queue("schedule.catch_up", { start: String(data.get("start")), end: String(data.get("end")) }); }}><label>Catch up from<input name="start" type="date" required defaultValue={text(schedule, "extended_start_suggestion", "")} /></label><label>through<input name="end" type="date" required defaultValue={text(schedule, "today", "")} /></label><button className="button button-small" disabled={disabled}>Queue catch-up</button></form></section>;
}
