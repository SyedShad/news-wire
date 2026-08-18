import assert from "node:assert/strict";
import { createHash, createHmac } from "node:crypto";
import test from "node:test";
import { verifyBridgeRequest } from "../lib/bridge/auth.ts";
import { parseBridgeSync, parseSnapshot, validateOwnerCommand } from "../lib/dashboard/validation.ts";

class NonceStatement {
  constructor(database, sql) {
    this.database = database;
    this.sql = sql.replace(/\s+/gu, " ").trim();
    this.values = [];
  }

  bind(...values) {
    this.values = values;
    return this;
  }

  async run() {
    if (this.sql.startsWith("INSERT INTO bridge_nonces")) {
      const nonce = this.values[0];
      if (this.database.nonces.has(nonce)) throw new Error("duplicate nonce");
      this.database.nonces.add(nonce);
    }
    return { success: true, meta: { changes: 1 } };
  }
}

class NonceD1 {
  constructor() { this.nonces = new Set(); }
  prepare(sql) { return new NonceStatement(this, sql); }
  async batch(statements) { return statements.map(() => ({ success: true })); }
}

function signedRequest(secret, body, nonce = "test_nonce_value_1234567890") {
  const timestamp = String(Date.now());
  const path = "/api/bridge/snapshot";
  const hash = createHash("sha256").update(body).digest("hex");
  const canonical = ["v1", timestamp, nonce, "PUT", path, hash].join("\n");
  const signature = createHmac("sha256", secret).update(canonical).digest("hex");
  return new Request(`https://dashboard.example.test${path}`, {
    method: "PUT",
    headers: {
      "x-news-wire-timestamp": timestamp,
      "x-news-wire-nonce": nonce,
      "x-news-wire-signature": signature,
      "x-news-wire-bridge-version": "2.0.0",
      "x-news-wire-runtime-version": "0.4.1",
    },
    body,
  });
}

test("bridge signatures are verified and each nonce is accepted only once", async () => {
  const secret = "test-bridge-secret-with-at-least-thirty-two-characters";
  const database = new NonceD1();
  const source = { DB: database, BRIDGE_SECRET: secret };
  const body = JSON.stringify({ schema_version: 1 });
  const first = await verifyBridgeRequest(signedRequest(secret, body), source);
  assert.equal(first.bodyText, body);
  await assert.rejects(
    verifyBridgeRequest(signedRequest(secret, body), source),
    /bridge_replay_detected/,
  );
});

test("dashboard snapshots and owner operations reject incomplete or unsupported input", () => {
  const snapshot = {
    schema_version: 1,
    generated_at: "2026-08-03T00:00:00Z",
    runtime_version: "0.4.0",
    overview: {
      counts: {}, sources: {}, queue_count: 0, queue_lag: "Clear",
      top_stories: [], alerts: [], scans: [],
    },
    stories: [], drafts: [], sources: [], schedule: {}, diagnostics: [],
  };
  assert.equal(parseSnapshot(snapshot).schema_version, 1);
  assert.throws(() => parseSnapshot({ schema_version: 1 }), /incomplete/);
  assert.deepEqual(
    validateOwnerCommand({ operation: "source.toggle", payload: { source_id: "source-1" } }),
    { operation: "source.toggle", payload: { source_id: "source-1" } },
  );
  assert.throws(
    () => validateOwnerCommand({ operation: "runtime.shell", payload: {} }),
    /Unsupported owner operation/,
  );
  assert.throws(
    () => validateOwnerCommand({ operation: "bridge.stop", payload: {} }),
    /Unsupported owner operation/,
  );
  const state = parseBridgeSync({
    schema_version: 2,
    kind: "state",
    sync_id: "0123456789abcdef",
    story_digest: "a".repeat(64),
    story_total: 0,
    resource_digest: "b".repeat(64),
    resource_total: 0,
    snapshot,
  });
  assert.equal(state.kind, "state");
  const delta = parseBridgeSync({
    schema_version: 2,
    kind: "stories",
    sync_id: "0123456789abcdef",
    mode: "delta",
    stories: [{ id: "story-2" }],
    deleted_ids: ["story-1"],
  });
  assert.deepEqual(delta.deleted_ids, ["story-1"]);
  assert.equal(delta.mode, "delta");
  assert.throws(
    () => parseBridgeSync({
      schema_version: 2,
      kind: "stories",
      sync_id: "0123456789abcdef",
      mode: "full",
      stories: [],
      deleted_ids: ["story-1"],
    }),
    /full story sync cannot delete ids/,
  );
  assert.throws(() => parseBridgeSync({ schema_version: 1 }), /Unsupported bridge sync schema/);
});
