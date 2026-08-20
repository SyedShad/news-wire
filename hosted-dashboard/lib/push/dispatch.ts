import type { AuthRuntimeEnv } from "../auth/types.ts";
import {
  decryptSubscription,
  encryptWebPush,
  MAX_PUSH_JSON_BYTES,
  pushConfiguration,
  vapidHeaders,
} from "./crypto.ts";
import {
  claimPushDeliveries,
  createDeliveryActionCapabilities,
  disablePushSubscription,
  markPushDeliveryDelivered,
  markPushDeliveryFailed,
  markPushDeliveryRetry,
} from "./store.ts";
import type { ArticleAlertPayload, PushPayload } from "./types.ts";

function encodedLength(value: string): number {
  return new TextEncoder().encode(value).byteLength;
}

function boundedPayload(payload: PushPayload): string {
  let candidate = JSON.stringify(payload);
  if (encodedLength(candidate) <= MAX_PUSH_JSON_BYTES) return candidate;
  const maximumContext = Math.max(80, payload.context.length - (encodedLength(candidate) - MAX_PUSH_JSON_BYTES) - 16);
  candidate = JSON.stringify({ ...payload, context: `${payload.context.slice(0, maximumContext).trimEnd()}…` });
  if (encodedLength(candidate) > MAX_PUSH_JSON_BYTES) throw new Error("push_payload_too_large");
  return candidate;
}

export async function dispatchPendingNotifications(source: AuthRuntimeEnv): Promise<void> {
  const config = pushConfiguration(source);
  if (!config?.enabled) return;
  const deliveries = await claimPushDeliveries(source);
  for (const delivery of deliveries) {
    let subscription;
    try {
      subscription = await decryptSubscription(config, delivery.subscriptionCiphertext);
    } catch {
      await disablePushSubscription(source.DB, delivery.subscriptionId, 0);
      continue;
    }
    try {
      let payload = delivery.payload;
      if (delivery.kind === "article_alert") {
        const capabilities = await createDeliveryActionCapabilities(source.DB, config, delivery);
        payload = {
          ...payload as ArticleAlertPayload,
          actions: {
            startResearch: { capability: capabilities.startResearch },
            dismiss: { capability: capabilities.dismiss },
          },
        };
      }
      const json = boundedPayload(payload);
      const body = await encryptWebPush(subscription, json);
      const response = await fetch(subscription.endpoint, {
        method: "POST",
        headers: await vapidHeaders(config, subscription.endpoint),
        body,
        redirect: "manual",
        cache: "no-store",
      });
      if (response.status >= 200 && response.status < 300) {
        await markPushDeliveryDelivered(source.DB, delivery.deliveryId, delivery.subscriptionId, response.status);
      } else if (response.status === 404 || response.status === 410) {
        await disablePushSubscription(source.DB, delivery.subscriptionId, response.status);
      } else if (response.status === 408 || response.status === 425 || response.status === 429 || response.status >= 500) {
        await markPushDeliveryRetry(
          source.DB, delivery.deliveryId, delivery.attemptCount, response.status, "provider_transient_failure",
        );
      } else {
        await markPushDeliveryFailed(source.DB, delivery.deliveryId, response.status, "provider_rejected_delivery");
      }
    } catch (error) {
      const permanent = error instanceof Error && new Set([
        "push_payload_too_large", "subscription_keys_invalid",
      ]).has(error.message);
      if (permanent) {
        await markPushDeliveryFailed(source.DB, delivery.deliveryId, null, error instanceof Error ? error.message : "delivery_invalid");
      } else {
        await markPushDeliveryRetry(
          source.DB, delivery.deliveryId, delivery.attemptCount, null, "provider_network_failure",
        );
      }
    }
  }
}
