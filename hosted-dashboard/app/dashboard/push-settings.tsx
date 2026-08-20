"use client";

import { useEffect, useState } from "react";
import {
  currentPushEndpoint,
  disablePushNotifications,
  enablePushNotifications,
  isIosWithoutHomeScreen,
  pushSupported,
  readPushRuntimeStatus,
  readPushSubscriptionStatus,
  sendPushCanary,
  setPushRuntimeAction,
  type PushRuntimeAction,
  type PushRuntimeStatus,
  type PushSubscriptionStatus,
} from "@/lib/push/client.ts";

type Operation = "enable" | "device" | "all" | "canary" | PushRuntimeAction | null;

function deviceDescription(count: number): string {
  if (count === 0) return "No subscribed devices";
  return `${count.toLocaleString()} subscribed ${count === 1 ? "device" : "devices"}`;
}

const MODE_LABELS = {
  shadow: "Shadow review",
  active: "Live alerts",
  paused: "Live alerts paused",
} as const;

function rolloutDate(value: number): string {
  if (!value) return "Start time unavailable";
  return `Current mode began ${new Date(value).toLocaleString()}`;
}

export default function PushSettings() {
  const [status, setStatus] = useState<PushSubscriptionStatus | null>(null);
  const [runtime, setRuntime] = useState<PushRuntimeStatus | null>(null);
  const [thisDeviceEnabled, setThisDeviceEnabled] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Operation>(null);
  const supported = pushSupported();
  const iosNeedsInstall = isIosWithoutHomeScreen();

  useEffect(() => {
    let active = true;
    Promise.all([readPushSubscriptionStatus(), currentPushEndpoint(), readPushRuntimeStatus()])
      .then(([nextStatus, endpoint, nextRuntime]) => {
        if (!active) return;
        setStatus(nextStatus);
        setThisDeviceEnabled(Boolean(endpoint));
        setRuntime(nextRuntime);
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "Could not read notification settings.");
      });
    return () => { active = false; };
  }, []);

  async function run(operation: Exclude<Operation, null>, work: () => Promise<void>) {
    setBusy(operation);
    setError(null);
    setMessage(null);
    try {
      await work();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The notification request failed.");
    } finally {
      setBusy(null);
    }
  }

  function enable() {
    void run("enable", async () => {
      if (iosNeedsInstall) throw new Error("On iPhone or iPad, add this dashboard to your Home Screen before enabling notifications.");
      if (!status) throw new Error("Notification settings are still loading.");
      const nextStatus = await enablePushNotifications(status.vapidPublicKey);
      setStatus({ ...nextStatus, vapidPublicKey: status.vapidPublicKey });
      setThisDeviceEnabled(true);
      setMessage("Notifications are enabled on this device.");
    });
  }

  function disable(scope: "device" | "all") {
    void run(scope, async () => {
      const nextStatus = await disablePushNotifications(scope);
      setStatus((current) => ({ ...nextStatus, vapidPublicKey: current?.vapidPublicKey || "" }));
      setThisDeviceEnabled(false);
      setMessage(scope === "all" ? "Notifications are disabled on every device." : "Notifications are disabled on this device.");
    });
  }

  function canary() {
    void run("canary", async () => {
      const endpoint = await currentPushEndpoint();
      if (!endpoint) throw new Error("This device is not subscribed.");
      await sendPushCanary(endpoint);
      setMessage("A test notification is queued for this device.");
    });
  }

  function changeRuntime(action: PushRuntimeAction) {
    if (action === "activate" && !window.confirm("Activate relevant-item alerts now? Continue only after a successful test notification and a completed 72-hour shadow review.")) return;
    void run(action, async () => {
      const nextRuntime = await setPushRuntimeAction(action);
      setRuntime(nextRuntime);
      setMessage(action === "activate"
        ? "Relevant-item alerts are live. Only newly released events after this activation are eligible for delivery."
        : action === "pause"
          ? "Live alerts are paused. Your device subscriptions remain available."
          : "Shadow review is on. Live alerts remain off until you activate them manually.");
    });
  }

  const serverReady = Boolean(status?.enabled && status.vapidPublicKey);
  return (
    <section className="panel setting-card span-two push-setting-card" aria-labelledby="push-settings-heading">
      <div className="panel-heading">
        <div><p className="section-index">06</p><h2 id="push-settings-heading">News notifications</h2></div>
        <span className={`status-chip ${thisDeviceEnabled ? "status-healthy" : "status-paused"}`}>
          {thisDeviceEnabled ? "Enabled here" : "Off on this device"}
        </span>
      </div>
      <div className="push-setting-body">
        <div className="push-setting-copy">
          <p>Receive every newly detected relevant item that matches the News Wire’s coverage, including articles, announcements, papers, repositories, newsletters, and discovery signals. Each notification includes the original title, publisher, category, published and detected times, context provenance, and source-derived context so you can review it directly.</p>
          <p className="push-privacy-warning"><strong>Lock-screen privacy:</strong> Full titles and news context may appear on your lock screen and other notification previews.</p>
          {iosNeedsInstall ? <p className="push-platform-note">On iPhone or iPad, use <strong>Add to Home Screen</strong>, open the installed News Wire, then enable notifications here.</p> : null}
          {!supported ? <p className="owner-warning">This browser cannot receive Web Push notifications.</p> : null}
          {status ? <small className="push-device-count">{deviceDescription(status.deviceCount)}</small> : <small className="push-device-count">Reading device status…</small>}
        </div>
        <div className="push-setting-actions" aria-label="Notification controls">
          {!thisDeviceEnabled ? (
            <button className="button button-small button-push-primary" type="button" disabled={!supported || iosNeedsInstall || !serverReady || busy !== null} onClick={enable}>
              {busy === "enable" ? "Enabling…" : "Enable notifications"}
            </button>
          ) : (
            <>
              <button className="button button-small" type="button" disabled={busy !== null} onClick={canary}>{busy === "canary" ? "Queuing…" : "Send test notification"}</button>
              <button className="button button-small" type="button" disabled={busy !== null} onClick={() => disable("device")}>{busy === "device" ? "Disabling…" : "Disable this device"}</button>
            </>
          )}
          {Boolean(status?.deviceCount) ? <button className="button button-small button-danger" type="button" disabled={busy !== null} onClick={() => disable("all")}>{busy === "all" ? "Disabling…" : "Disable all devices"}</button> : null}
        </div>
      </div>
      <div className="push-setting-feedback" aria-live="polite">
        {message ? <p className="owner-message">{message}</p> : null}
        {error ? <p className="owner-warning">{error}</p> : null}
      </div>
      <div className="push-rollout" aria-labelledby="push-rollout-heading">
        <div className="push-rollout-copy">
          <div className="push-rollout-title"><h3 id="push-rollout-heading">Live-alert rollout</h3><span className={`status-chip ${runtime?.mode === "active" ? "status-healthy" : runtime?.mode === "paused" ? "status-warning" : "status-paused"}`}>{runtime ? MODE_LABELS[runtime.mode] : "Reading status…"}</span></div>
          <p>Device setup and test notifications do not turn on live alerts. A successful test notification and a completed 72-hour shadow review are required before activation. The News Wire never activates automatically.</p>
          <small>{runtime ? rolloutDate(runtime.activationWatermark) : "Reading rollout start time…"}</small>
        </div>
        <div className="push-rollout-actions" aria-label="Live-alert rollout controls">
          <button className="button button-small button-push-primary" type="button" disabled={!runtime || !runtime.enabled || runtime.mode === "active" || busy !== null} onClick={() => changeRuntime("activate")}>{busy === "activate" ? "Activating…" : "Activate relevant-item alerts (manual)"}</button>
          <button className="button button-small" type="button" disabled={!runtime || runtime.mode !== "active" || busy !== null} onClick={() => changeRuntime("pause")}>{busy === "pause" ? "Pausing…" : "Pause live alerts"}</button>
          <button className="button button-small" type="button" disabled={!runtime || runtime.mode === "shadow" || busy !== null} onClick={() => changeRuntime("shadow")}>{busy === "shadow" ? "Returning…" : "Return to shadow"}</button>
        </div>
      </div>
    </section>
  );
}
