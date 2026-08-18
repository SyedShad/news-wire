import assert from "node:assert/strict";
import test from "node:test";
import {
  base64UrlEncode,
  derivePbkdf2,
  parsePasswordVerifier,
  verifyPassword,
} from "../lib/auth/crypto.ts";
import { approvedEmailDomain } from "../lib/auth/policy.ts";
import {
  accessProfile,
  can,
  canMutate,
} from "../lib/auth/authorization.ts";
import { isSameOriginRequest } from "../lib/auth/http.ts";

test("allows both exact Workspace domains and rejects all others", () => {
  assert.equal(approvedEmailDomain("person@sentient.foundation"), "sentient.foundation");
  assert.equal(approvedEmailDomain("person@sentient.xyz"), "sentient.xyz");
  assert.equal(approvedEmailDomain("person@SENTIENT.XYZ"), "sentient.xyz");
  assert.equal(approvedEmailDomain("person@gmail.com"), null);
  assert.equal(approvedEmailDomain("person@sub.sentient.xyz"), null);
  assert.equal(approvedEmailDomain("person@sentient.xyz.example.com"), null);
  assert.equal(approvedEmailDomain("sentient.xyz"), null);
});

test("verifies a slow salted master-password digest", async () => {
  const salt = Uint8Array.from({ length: 24 }, (_, index) => index + 1);
  const digest = await derivePbkdf2("a sufficiently long test password", salt, 100_000);
  const verifier = [
    "pbkdf2-sha256",
    "100000",
    base64UrlEncode(salt),
    base64UrlEncode(digest),
  ].join(":");

  assert.equal(await verifyPassword("a sufficiently long test password", verifier), true);
  assert.equal(await verifyPassword("wrong password", verifier), false);
  assert.equal(parsePasswordVerifier(verifier).iterations, 100_000);
  assert.throws(
    () => parsePasswordVerifier(verifier.replace("100000", "99999")),
    /Invalid master password verifier format/,
  );
});

test("accepts the Sites opaque origin only with authenticated same-site dispatch metadata", () => {
  const source = {
    AUTH_BASE_URL: "https://dashboard.example.chatgpt.site",
  };
  const headers = {
    origin: "null",
    "sec-fetch-site": "same-origin",
    "x-dispatched-app": "dashboard-example",
    "oai-authenticated-user-id": "owner-account-id",
  };
  assert.equal(
    isSameOriginRequest(
      new Request("https://dashboard.example.chatgpt.site/api/auth/master", { headers }),
      source,
    ),
    true,
  );
  assert.equal(
    isSameOriginRequest(
      new Request("https://dashboard.example.chatgpt.site/api/auth/master", {
        headers: { ...headers, "sec-fetch-site": "cross-site" },
      }),
      source,
    ),
    false,
  );
  assert.equal(
    isSameOriginRequest(
      new Request("https://dashboard.example.chatgpt.site/api/auth/master", {
        headers: { ...headers, "oai-authenticated-user-id": "" },
      }),
      source,
    ),
    false,
  );
  assert.equal(
    isSameOriginRequest(
      new Request("https://dashboard.example.chatgpt.site/api/auth/master", {
        headers: { ...headers, origin: "https://attacker.example" },
      }),
      source,
    ),
    false,
  );
});

test("Sentient accounts are view-only and owner access has every capability", () => {
  const editor = accessProfile("editor");
  const master = accessProfile("master");

  assert.equal(editor.mode, "view_only");
  assert.equal(master.mode, "full_control");
  assert.equal(can("editor", "dashboard.view"), true);
  assert.equal(can("editor", "stories.review"), false);
  assert.equal(canMutate("editor"), false);
  assert.equal(canMutate("master"), true);
  assert.ok(master.capabilities.length > editor.capabilities.length);
  for (const capability of editor.capabilities) {
    assert.equal(can("master", capability), true);
  }
});
