import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("Google callback performs the required server-side identity checks", async () => {
  const [google, callback, master, gitignore] = await Promise.all([
    readFile(new URL("lib/auth/google.ts", root), "utf8"),
    readFile(new URL("app/api/auth/google/callback/route.ts", root), "utf8"),
    readFile(new URL("app/api/auth/master/route.ts", root), "utf8"),
    readFile(new URL(".gitignore", root), "utf8"),
  ]);
  for (const required of [
    "jwtVerify",
    "issuer",
    "audience",
    "algorithms",
    "clockTolerance",
    "payload.nonce",
    "email_verified",
    "payload.hd",
    "approvedEmailDomain",
  ]) {
    assert.match(google, new RegExp(required.replace(".", "\\.")));
  }
  assert.match(callback, /consumeOauthTransaction/);
  assert.match(callback, /role:\s*["']editor["']/);
  assert.match(master, /assertSameOrigin/);
  assert.match(master, /temporarily_locked/);
  assert.match(master, /role:\s*["']master["']/);
  assert.match(gitignore, /\.env\*/);
});

test("ChatGPT host authentication is not present", async () => {
  await assert.rejects(access(new URL("app/chatgpt-auth.ts", root)));
});

test("dashboard APIs enforce session roles on the server", async () => {
  const [apiHelper, accessRoute, ownerRoute, authorization] = await Promise.all([
    readFile(new URL("lib/auth/api.ts", root), "utf8"),
    readFile(new URL("app/api/access/route.ts", root), "utf8"),
    readFile(new URL("app/api/owner/access/route.ts", root), "utf8"),
    readFile(new URL("lib/auth/authorization.ts", root), "utf8"),
  ]);
  assert.match(apiHelper, /readSession/);
  assert.match(apiHelper, /authentication_required/);
  assert.match(apiHelper, /owner_access_required/);
  assert.match(accessRoute, /authorizeApiRequest\(request\)/);
  assert.match(ownerRoute, /authorizeApiRequest\(request, \["master"\]\)/);
  assert.match(authorization, /mode:\s*"view_only"/);
  assert.match(authorization, /mode:\s*"full_control"/);
});

test("deployment migrations enforce authorization and singleton invariants", async () => {
  const [migration, projectionMigration, resourceMigration] = await Promise.all([
    readFile(new URL("drizzle/0003_dark_master_mold.sql", root), "utf8"),
    readFile(new URL("drizzle/0004_rare_ken_ellis.sql", root), "utf8"),
    readFile(new URL("drizzle/0005_flawless_colleen_wing.sql", root), "utf8"),
  ]);
  for (const constraint of [
    "auth_sessions_role_check",
    "audit_events_role_check",
    "bridge_status_singleton_check",
    "command_queue_status_check",
    "command_queue_requested_role_check",
    "dashboard_state_singleton_check",
  ]) {
    assert.ok(migration.includes(`CONSTRAINT "${constraint}"`));
  }
  for (const constraint of ["projection_state_singleton_check", "read_request_status_check", "read_request_resource_type_check"]) {
    assert.ok(projectionMigration.includes(`CONSTRAINT "${constraint}"`));
  }
  assert.match(projectionMigration, /expires_at.*DEFAULT 0 NOT NULL/);
  assert.match(resourceMigration, /CREATE TABLE `resource_projection`/);
  assert.match(resourceMigration, /resource_digest.*DEFAULT '' NOT NULL/);
});
