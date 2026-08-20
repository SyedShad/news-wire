import { SignJWT, importJWK, type JWK } from "jose";
import {
  base64UrlDecode,
  base64UrlEncode,
  hmacSha256,
  randomToken,
  sha256,
} from "../auth/crypto.ts";
import type { AuthRuntimeEnv } from "../auth/types.ts";
import type { StoredPushSubscription } from "./types.ts";

const encoder = new TextEncoder();
const SUBSCRIPTION_AAD = encoder.encode("osainw-push-subscription:v1");
export const MAX_PUSH_JSON_BYTES = 2_700;
export const MAX_ENCRYPTED_PUSH_BYTES = 3_072;

export type PushConfiguration = {
  enabled: boolean;
  vapidPublicKey: string;
  vapidPrivateKey: string;
  vapidSubject: string;
  subscriptionEncryptionKey: Uint8Array;
  actionSecret: string;
};

function configured(source: AuthRuntimeEnv, name: keyof AuthRuntimeEnv): string {
  const value = source[name];
  return typeof value === "string" ? value.trim() : "";
}

export function pushConfiguration(source: AuthRuntimeEnv): PushConfiguration | null {
  const vapidPublicKey = configured(source, "PUSH_VAPID_PUBLIC_KEY");
  const vapidPrivateKey = configured(source, "PUSH_VAPID_PRIVATE_KEY");
  const vapidSubject = configured(source, "PUSH_VAPID_SUBJECT");
  const encryptionValue = configured(source, "PUSH_SUBSCRIPTION_ENCRYPTION_KEY");
  const actionSecret = configured(source, "PUSH_ACTION_SECRET");
  if (!vapidPublicKey || !vapidPrivateKey || !vapidSubject || !encryptionValue || !actionSecret) return null;
  try {
    const publicBytes = base64UrlDecode(vapidPublicKey);
    const privateBytes = base64UrlDecode(vapidPrivateKey);
    const encryptionBytes = base64UrlDecode(encryptionValue);
    if (publicBytes.length !== 65 || publicBytes[0] !== 4 || privateBytes.length !== 32 ||
        encryptionBytes.length !== 32 || actionSecret.length < 32) return null;
    const subject = new URL(vapidSubject);
    if (!new Set(["mailto:", "https:"]).has(subject.protocol)) return null;
    return {
      enabled: configured(source, "PUSH_DELIVERY_ENABLED").toLowerCase() === "true",
      vapidPublicKey,
      vapidPrivateKey,
      vapidSubject,
      subscriptionEncryptionKey: encryptionBytes,
      actionSecret,
    };
  } catch {
    return null;
  }
}

async function aesKey(raw: Uint8Array): Promise<CryptoKey> {
  return crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["encrypt", "decrypt"]);
}

export async function encryptSubscription(
  config: PushConfiguration,
  subscription: StoredPushSubscription,
): Promise<string> {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const plaintext = encoder.encode(JSON.stringify(subscription));
  const encrypted = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv, additionalData: SUBSCRIPTION_AAD, tagLength: 128 },
    await aesKey(config.subscriptionEncryptionKey),
    plaintext,
  );
  return `v1.${base64UrlEncode(iv)}.${base64UrlEncode(new Uint8Array(encrypted))}`;
}

export async function decryptSubscription(
  config: PushConfiguration,
  ciphertext: string,
): Promise<StoredPushSubscription> {
  const [version, ivText, bodyText, extra] = ciphertext.split(".");
  if (version !== "v1" || !ivText || !bodyText || extra !== undefined) throw new Error("subscription_ciphertext_invalid");
  const decrypted = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: base64UrlDecode(ivText), additionalData: SUBSCRIPTION_AAD, tagLength: 128 },
    await aesKey(config.subscriptionEncryptionKey),
    base64UrlDecode(bodyText),
  );
  const parsed = JSON.parse(new TextDecoder().decode(decrypted)) as StoredPushSubscription;
  if (!parsed || typeof parsed.endpoint !== "string" || typeof parsed.id !== "string" ||
      typeof parsed.keys?.p256dh !== "string" || typeof parsed.keys?.auth !== "string") {
    throw new Error("subscription_ciphertext_invalid");
  }
  return parsed;
}

export function newActionCapability(): string {
  return randomToken(32);
}

export async function actionCapabilityHash(config: PushConfiguration, capability: string): Promise<string> {
  return hmacSha256(config.actionSecret, `push-action:${capability}`);
}

export async function subscriptionEndpointHash(endpoint: string): Promise<string> {
  return sha256(`push-endpoint:${endpoint}`);
}

async function hkdf(
  inputKeyMaterial: Uint8Array,
  salt: Uint8Array,
  info: Uint8Array,
  length: number,
): Promise<Uint8Array> {
  const key = await crypto.subtle.importKey("raw", inputKeyMaterial, "HKDF", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits(
    { name: "HKDF", hash: "SHA-256", salt, info },
    key,
    length * 8,
  );
  return new Uint8Array(bits);
}

function concat(...arrays: Uint8Array[]): Uint8Array {
  const length = arrays.reduce((total, item) => total + item.length, 0);
  const result = new Uint8Array(length);
  let offset = 0;
  for (const item of arrays) {
    result.set(item, offset);
    offset += item.length;
  }
  return result;
}

function contentInfo(label: string): Uint8Array {
  return encoder.encode(`Content-Encoding: ${label}\0`);
}

export async function encryptWebPush(
  subscription: StoredPushSubscription,
  json: string,
): Promise<Uint8Array> {
  const plaintextJson = encoder.encode(json);
  if (plaintextJson.byteLength > MAX_PUSH_JSON_BYTES) throw new Error("push_payload_too_large");
  const clientPublic = base64UrlDecode(subscription.keys.p256dh);
  const authSecret = base64UrlDecode(subscription.keys.auth);
  if (clientPublic.length !== 65 || clientPublic[0] !== 4 || authSecret.length !== 16) {
    throw new Error("subscription_keys_invalid");
  }
  const clientKey = await crypto.subtle.importKey(
    "raw", clientPublic, { name: "ECDH", namedCurve: "P-256" }, false, [],
  );
  const serverKeys = await crypto.subtle.generateKey(
    { name: "ECDH", namedCurve: "P-256" }, true, ["deriveBits"],
  ) as CryptoKeyPair;
  const serverPublic = new Uint8Array(await crypto.subtle.exportKey("raw", serverKeys.publicKey));
  const shared = new Uint8Array(await crypto.subtle.deriveBits(
    { name: "ECDH", public: clientKey }, serverKeys.privateKey, 256,
  ));
  const keyInfo = concat(encoder.encode("WebPush: info\0"), clientPublic, serverPublic);
  const inputKeyMaterial = await hkdf(shared, authSecret, keyInfo, 32);
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const contentEncryptionKey = await hkdf(inputKeyMaterial, salt, contentInfo("aes128gcm"), 16);
  const nonce = await hkdf(inputKeyMaterial, salt, contentInfo("nonce"), 12);
  const plaintext = concat(plaintextJson, new Uint8Array([2]));
  const ciphertext = new Uint8Array(await crypto.subtle.encrypt(
    { name: "AES-GCM", iv: nonce, tagLength: 128 },
    await aesKey(contentEncryptionKey),
    plaintext,
  ));
  const recordSize = new Uint8Array(4);
  new DataView(recordSize.buffer).setUint32(0, 4_096);
  const body = concat(salt, recordSize, new Uint8Array([serverPublic.length]), serverPublic, ciphertext);
  if (body.byteLength > MAX_ENCRYPTED_PUSH_BYTES) throw new Error("push_payload_too_large");
  return body;
}

export async function vapidHeaders(
  config: PushConfiguration,
  endpoint: string,
): Promise<Record<string, string>> {
  const publicBytes = base64UrlDecode(config.vapidPublicKey);
  const privateBytes = base64UrlDecode(config.vapidPrivateKey);
  const key = await importJWK({
    kty: "EC",
    crv: "P-256",
    x: base64UrlEncode(publicBytes.slice(1, 33)),
    y: base64UrlEncode(publicBytes.slice(33, 65)),
    d: base64UrlEncode(privateBytes),
  } satisfies JWK, "ES256");
  const audience = new URL(endpoint).origin;
  const jwt = await new SignJWT({})
    .setProtectedHeader({ alg: "ES256", typ: "JWT" })
    .setAudience(audience)
    .setSubject(config.vapidSubject)
    .setExpirationTime(Math.floor(Date.now() / 1_000) + 12 * 60 * 60)
    .sign(key);
  return {
    authorization: `vapid t=${jwt}, k=${config.vapidPublicKey}`,
    "content-encoding": "aes128gcm",
    "content-type": "application/octet-stream",
    ttl: "86400",
    urgency: "normal",
  };
}
