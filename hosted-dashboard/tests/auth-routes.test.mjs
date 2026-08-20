import assert from "node:assert/strict";
import { pbkdf2Sync } from "node:crypto";
import test from "node:test";

class MemoryStatement {
  constructor(database, sql) {
    this.database = database;
    this.sql = sql.replace(/\s+/gu, " ").trim();
    this.values = [];
  }

  bind(...values) {
    this.values = values;
    return this;
  }

  async first() {
    if (this.sql.includes("FROM master_login_state")) {
      return this.database.masterStates.get(this.values[0]) ?? null;
    }
    if (this.sql.includes("FROM auth_sessions")) {
      return this.database.sessions.get(this.values[0]) ?? null;
    }
    if (this.sql.includes("FROM oauth_transactions")) {
      return this.database.oauthTransactions.get(this.values[0]) ?? null;
    }
    return null;
  }

  async all() {
    return { success: true, results: [] };
  }

  async run() {
    if (this.sql.startsWith("INSERT INTO master_login_state")) {
      const [fingerprint, failures, nextAllowedAt, lockedUntil] = this.values;
      this.database.masterStates.set(fingerprint, {
        failures,
        next_allowed_at: nextAllowedAt,
        locked_until: lockedUntil,
      });
    } else if (this.sql.startsWith("DELETE FROM master_login_state")) {
      this.database.masterStates.delete(this.values[0]);
    } else if (this.sql.startsWith("INSERT INTO auth_sessions")) {
      const [sessionHash, actorId, role, email, credentialVersion, createdAt, expiresAt] = this.values;
      this.database.sessions.set(sessionHash, {
        actor_id: actorId,
        role,
        email,
        credential_version: credentialVersion,
        created_at: createdAt,
        expires_at: expiresAt,
      });
    } else if (this.sql.startsWith("DELETE FROM auth_sessions")) {
      this.database.sessions.delete(this.values[0]);
    } else if (this.sql.startsWith("INSERT INTO oauth_transactions")) {
      const [stateHash, verifier, nonce, createdAt, expiresAt] = this.values;
      this.database.oauthTransactions.set(stateHash, {
        verifier,
        nonce,
        created_at: createdAt,
        expires_at: expiresAt,
      });
    } else if (this.sql.startsWith("DELETE FROM oauth_transactions")) {
      this.database.oauthTransactions.delete(this.values[0]);
    } else if (this.sql.startsWith("INSERT INTO audit_events")) {
      this.database.audits.push(this.values);
    }
    return { success: true, meta: { changes: 1 } };
  }
}

class MemoryD1 {
  constructor() {
    this.masterStates = new Map();
    this.sessions = new Map();
    this.oauthTransactions = new Map();
    this.audits = [];
  }

  prepare(sql) {
    return new MemoryStatement(this, sql);
  }

  async batch(statements) {
    return statements.map(() => ({ success: true }));
  }
}

const salt = Buffer.alloc(24, 7);
const masterPassword = "test-only-master-password-with-enough-entropy";
const masterVerifier = [
  "pbkdf2-sha256",
  "100000",
  salt.toString("base64url"),
  pbkdf2Sync(masterPassword, salt, 100_000, 32, "sha256").toString("base64url"),
].join(":");

const database = new MemoryD1();
const baseEnvironment = {
  ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) },
  DB: database,
  CONTENT: {},
  AUTH_BASE_URL: "http://localhost",
  AUTH_SECRET: "test-auth-secret-that-is-long-and-random-enough",
  GOOGLE_CLIENT_ID: "google-client-id.apps.googleusercontent.com",
  GOOGLE_CLIENT_SECRET: "test-client-secret",
  MASTER_PASSWORD_VERIFIER: masterVerifier,
  MASTER_PASSWORD_VERSION: "1",
};

const workerUrl = new URL("../dist/server/index.js", import.meta.url);
workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
const { default: worker } = await import(workerUrl.href);
const context = { waitUntil() {}, passThroughOnException() {} };

function fetchApp(path, init = {}, environment = baseEnvironment) {
  globalThis.__CLOUDFLARE_TEST_ENV__ = environment;
  return worker.fetch(
    new Request(`${new URL(environment.AUTH_BASE_URL).origin}${path}`, init),
    environment,
    context,
  );
}

function masterRequest(password, ip, accept = "application/json") {
  return fetchApp("/api/auth/master", {
    method: "POST",
    headers: {
      accept,
      "content-type": "application/x-www-form-urlencoded",
      origin: "http://localhost",
      "cf-connecting-ip": ip,
    },
    body: new URLSearchParams({ password }),
    redirect: "manual",
  });
}

async function sessionCookie(role, email = null) {
  const token = `test-${role}-session-${crypto.randomUUID()}`;
  const hash = Buffer.from(
    await crypto.subtle.digest("SHA-256", new TextEncoder().encode(token)),
  ).toString("base64url");
  database.sessions.set(hash, {
    actor_id: role === "master" ? "master" : "google:test-editor",
    role,
    email,
    credential_version: role === "master" ? "1" : "google-v1",
    created_at: Date.now(),
    expires_at: Date.now() + 60_000,
  });
  return `osainw_session=${token}`;
}

test("login page exposes only owner entry with no Google or email field", async () => {
  const response = await fetchApp("/");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /Owner access/);
  assert.doesNotMatch(html, /Continue with Google/);
  assert.doesNotMatch(html, /sentient\.foundation/);
  assert.doesNotMatch(html, /sentient\.xyz/);
  assert.doesNotMatch(html, /type=["']email["']/i);
  assert.doesNotMatch(html, /name=["']email["']/i);
  assert.doesNotMatch(html, /(?:Sign in|Continue) with ChatGPT/i);
  assert.equal(response.headers.get("x-frame-options"), "DENY");
  assert.match(response.headers.get("content-security-policy") ?? "", /frame-ancestors 'none'/);
});

test("unauthenticated dashboard and session API do not return protected data", async () => {
  const dashboard = await fetchApp("/dashboard", { redirect: "manual" });
  assert.ok([303, 307, 308].includes(dashboard.status));
  assert.equal(new URL(dashboard.headers.get("location"), "http://localhost").pathname, "/");

  const session = await fetchApp("/api/session");
  assert.equal(session.status, 401);
  assert.deepEqual(await session.json(), { authenticated: false });

  for (const path of [
    "/api/dashboard/overview",
    "/api/dashboard/snapshot",
    "/api/dashboard/stories",
    "/api/dashboard/stories/fixture-story",
    "/api/dashboard/stories/fixture-story/evidence",
    "/api/dashboard/drafts",
    "/api/dashboard/drafts/1",
    "/api/dashboard/drafts/1/export",
    "/api/dashboard/drafts/1/export-html",
    "/api/dashboard/sources",
    "/api/dashboard/schedule",
    "/api/dashboard/settings",
    "/api/dashboard/diagnostics/export",
    "/api/push/subscriptions",
    "/api/push/status",
  ]) {
    const response = await fetchApp(path);
    assert.equal(response.status, 401, path);
    assert.doesNotMatch(await response.text(), /fixture-story|private dashboard datum/i, path);
  }
});

test("legacy editor sessions cannot enter the owner-only dashboard or owner APIs", async () => {
  const cookie = await sessionCookie("editor", "person@sentient.foundation");

  const access = await fetchApp("/api/access", { headers: { cookie } });
  assert.equal(access.status, 200);
  const payload = await access.json();
  assert.equal(payload.actor.role, "editor");
  assert.equal(payload.access.mode, "view_only");
  assert.ok(payload.access.capabilities.includes("dashboard.view"));
  assert.equal(payload.access.capabilities.includes("stories.review"), false);

  const owner = await fetchApp("/api/owner/access", { headers: { cookie } });
  assert.equal(owner.status, 403);
  assert.equal((await owner.json()).error, "owner_access_required");

  const dashboard = await fetchApp("/dashboard", { headers: { cookie }, redirect: "manual" });
  assert.ok([303, 307, 308].includes(dashboard.status));
});

test("master sessions receive full access and owner-only APIs", async () => {
  const cookie = await sessionCookie("master");

  const access = await fetchApp("/api/access", { headers: { cookie } });
  assert.equal(access.status, 200);
  const payload = await access.json();
  assert.equal(payload.actor.role, "master");
  assert.equal(payload.access.mode, "full_control");
  assert.ok(payload.access.capabilities.includes("stories.review"));
  assert.equal(payload.access.capabilities.includes("runtime.stop"), false);

  const owner = await fetchApp("/api/owner/access", { headers: { cookie } });
  assert.equal(owner.status, 200);
  assert.equal((await owner.json()).owner, true);

  const dashboard = await fetchApp("/dashboard", { headers: { cookie } });
  assert.equal(dashboard.status, 200);
  const html = await dashboard.text();
  assert.match(html, /Owner · Full access/);
  assert.match(html, /Full operational access/);
  assert.doesNotMatch(html, /View-only boundary/);
});

test("Google start uses state, nonce, and PKCE without an application email hint", async () => {
  const response = await fetchApp("/api/auth/google/start", { redirect: "manual" });
  assert.equal(response.status, 302);
  const target = new URL(response.headers.get("location"));
  assert.equal(target.origin, "https://accounts.google.com");
  assert.equal(target.pathname, "/o/oauth2/v2/auth");
  assert.ok(target.searchParams.get("state"));
  assert.ok(target.searchParams.get("nonce"));
  assert.ok(target.searchParams.get("code_challenge"));
  assert.equal(target.searchParams.get("code_challenge_method"), "S256");
  assert.equal(target.searchParams.has("login_hint"), false);
  assert.equal(target.searchParams.has("email"), false);
  assert.equal(target.searchParams.has("hd"), false);
  assert.match(response.headers.get("set-cookie") ?? "", /HttpOnly/i);
});

test("invalid Google callback state is rejected before token exchange", async () => {
  const response = await fetchApp(
    "/api/auth/google/callback?code=unused&state=forged",
    { redirect: "manual", headers: { "cf-connecting-ip": "203.0.113.10" } },
  );
  assert.equal(response.status, 303);
  const target = new URL(response.headers.get("location"));
  assert.equal(target.pathname, "/auth/error");
  assert.equal(target.searchParams.get("code"), "invalid_state");
});

test("master authentication covers failure, throttling, lockout, success, rotation, expiry, and logout", async () => {
  const failed = await masterRequest("wrong", "203.0.113.20");
  assert.equal(failed.status, 401);
  assert.equal((await failed.json()).error, "invalid_credentials");

  const throttled = await masterRequest("wrong", "203.0.113.20");
  assert.equal(throttled.status, 429);
  assert.ok(Number(throttled.headers.get("retry-after")) >= 1);

  database.masterStates.set(
    await crypto.subtle
      .importKey(
        "raw",
        new TextEncoder().encode(baseEnvironment.AUTH_SECRET),
        { name: "HMAC", hash: "SHA-256" },
        false,
        ["sign"],
      )
      .then(async (key) =>
        Buffer.from(
          await crypto.subtle.sign("HMAC", key, new TextEncoder().encode("ip:203.0.113.21")),
        ).toString("base64url"),
      ),
    { failures: 5, next_allowed_at: 0, locked_until: Date.now() + 60_000 },
  );
  const locked = await masterRequest(masterPassword, "203.0.113.21");
  assert.equal(locked.status, 423);

  const success = await masterRequest(masterPassword, "203.0.113.22");
  assert.equal(success.status, 200);
  const sessionCookie = (success.headers.get("set-cookie") ?? "").split(";", 1)[0];
  assert.match(sessionCookie, /^osainw_session=/);
  assert.match(success.headers.get("set-cookie") ?? "", /HttpOnly/i);
  assert.match(success.headers.get("set-cookie") ?? "", /SameSite=Lax/i);

  const session = await fetchApp("/api/session", { headers: { cookie: sessionCookie } });
  assert.equal(session.status, 200);
  assert.equal((await session.json()).actor.role, "master");

  const rotated = await fetchApp(
    "/api/session",
    { headers: { cookie: sessionCookie } },
    { ...baseEnvironment, MASTER_PASSWORD_VERSION: "2" },
  );
  assert.equal(rotated.status, 401);

  const secondSuccess = await masterRequest(masterPassword, "203.0.113.23");
  const expiringCookie = (secondSuccess.headers.get("set-cookie") ?? "").split(";", 1)[0];
  for (const stored of database.sessions.values()) stored.expires_at = Date.now() - 1;
  const expired = await fetchApp("/api/session", { headers: { cookie: expiringCookie } });
  assert.equal(expired.status, 401);

  const thirdSuccess = await masterRequest(masterPassword, "203.0.113.24");
  const logoutCookie = (thirdSuccess.headers.get("set-cookie") ?? "").split(";", 1)[0];
  const logout = await fetchApp("/api/auth/logout", {
    method: "POST",
    headers: { cookie: logoutCookie, origin: "http://localhost" },
    redirect: "manual",
  });
  assert.equal(logout.status, 303);
  const afterLogout = await fetchApp("/api/session", { headers: { cookie: logoutCookie } });
  assert.equal(afterLogout.status, 401);
  assert.ok(database.audits.length >= 5);
});

test("Sites sandboxed owner login and logout accept authenticated opaque-origin navigation", async () => {
  const environment = {
    ...baseEnvironment,
    AUTH_BASE_URL: "https://dashboard.example.chatgpt.site",
  };
  const sitesHeaders = {
    accept: "application/json",
    "content-type": "application/x-www-form-urlencoded",
    origin: "null",
    "sec-fetch-site": "same-origin",
    "sec-fetch-mode": "navigate",
    "sec-fetch-dest": "document",
    "x-dispatched-app": "dashboard-example",
    "oai-authenticated-user-id": "owner-account-id",
    "cf-connecting-ip": "203.0.113.30",
  };
  const login = await fetchApp(
    "/api/auth/master",
    {
      method: "POST",
      headers: sitesHeaders,
      body: new URLSearchParams({ password: masterPassword }),
      redirect: "manual",
    },
    environment,
  );
  assert.equal(login.status, 200);
  const cookie = (login.headers.get("set-cookie") ?? "").split(";", 1)[0];
  assert.match(cookie, /^osainw_session=/);

  const logout = await fetchApp(
    "/api/auth/logout",
    {
      method: "POST",
      headers: { ...sitesHeaders, cookie },
      redirect: "manual",
    },
    environment,
  );
  assert.equal(logout.status, 303);

  const rejected = await fetchApp(
    "/api/auth/master",
    {
      method: "POST",
      headers: { ...sitesHeaders, "sec-fetch-site": "cross-site" },
      body: new URLSearchParams({ password: masterPassword }),
      redirect: "manual",
    },
    environment,
  );
  assert.equal(rejected.status, 503);
});
