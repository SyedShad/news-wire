const encoder = new TextEncoder();

export function base64UrlEncode(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary)
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replace(/=+$/u, "");
}

export function base64UrlDecode(value: string): Uint8Array {
  const normalized = value.replaceAll("-", "+").replaceAll("_", "/");
  const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "=");
  const binary = atob(padded);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

export function randomToken(byteLength = 32): string {
  const bytes = new Uint8Array(byteLength);
  crypto.getRandomValues(bytes);
  return base64UrlEncode(bytes);
}

export async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", encoder.encode(value));
  return base64UrlEncode(new Uint8Array(digest));
}

export async function hmacSha256(secret: string, value: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign("HMAC", key, encoder.encode(value));
  return base64UrlEncode(new Uint8Array(signature));
}

export function constantTimeEqual(left: Uint8Array, right: Uint8Array): boolean {
  const length = Math.max(left.length, right.length);
  let mismatch = left.length ^ right.length;
  for (let index = 0; index < length; index += 1) {
    mismatch |= (left[index] ?? 0) ^ (right[index] ?? 0);
  }
  return mismatch === 0;
}

export async function derivePbkdf2(
  password: string,
  salt: Uint8Array,
  iterations: number,
  length = 32,
): Promise<Uint8Array> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(password),
    "PBKDF2",
    false,
    ["deriveBits"],
  );
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", hash: "SHA-256", salt, iterations },
    key,
    length * 8,
  );
  return new Uint8Array(bits);
}

export type ParsedPasswordVerifier = {
  iterations: number;
  salt: Uint8Array;
  digest: Uint8Array;
};

export function parsePasswordVerifier(value: string): ParsedPasswordVerifier {
  const [algorithm, iterationText, saltText, digestText, extra] = value.split(":");
  const iterations = Number(iterationText);
  if (
    algorithm !== "pbkdf2-sha256" ||
    !Number.isSafeInteger(iterations) ||
    iterations < 600_000 ||
    !saltText ||
    !digestText ||
    extra !== undefined
  ) {
    throw new Error("Invalid master password verifier format");
  }
  const salt = base64UrlDecode(saltText);
  const digest = base64UrlDecode(digestText);
  if (salt.length < 16 || digest.length < 32) {
    throw new Error("Master password verifier is below the security minimum");
  }
  return { iterations, salt, digest };
}

export async function verifyPassword(
  password: string,
  encodedVerifier: string,
): Promise<boolean> {
  const verifier = parsePasswordVerifier(encodedVerifier);
  const candidate = await derivePbkdf2(
    password,
    verifier.salt,
    verifier.iterations,
    verifier.digest.length,
  );
  return constantTimeEqual(candidate, verifier.digest);
}
