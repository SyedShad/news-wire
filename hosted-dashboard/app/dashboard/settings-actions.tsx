"use client";

import { CommandMessage, useOwnerCommand } from "./command-client.tsx";

export default function SettingsActions({ connected }: { connected: boolean }) {
  const { queue, message, busy } = useOwnerCommand();
  const disabled = busy || !connected;
  return <section className="owner-control-card"><div className="section-heading"><div><p className="eyebrow">Owner operations</p><h2>Maintenance</h2></div></div>{!connected ? <p className="owner-warning">Maintenance commands require a current laptop heartbeat.</p> : null}<CommandMessage message={message} /><div className="button-row control-actions"><button className="button button-small" disabled={disabled} onClick={() => queue("bridge.healthcheck", {})}>Run bridge healthcheck</button><button className="button button-small" disabled={disabled} onClick={() => queue("alerts.mark_read", {})}>Acknowledge alerts</button><button className="button button-small button-danger" disabled={disabled} onClick={() => queue("diagnostics.purge", { confirmation: "PURGE OPERATIONS", category_selected: true }, "Permanently purge local operational diagnostics?")}>Purge diagnostics</button></div></section>;
}
