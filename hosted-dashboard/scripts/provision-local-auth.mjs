import { pbkdf2Sync, randomBytes } from "node:crypto";
import { execFileSync } from "node:child_process";
import { open, stat } from "node:fs/promises";
import { resolve } from "node:path";

const outputPath = resolve(".env.local");
try {
  await stat(outputPath);
  throw new Error(
    ".env.local already exists. Refusing to overwrite it; rotate credentials deliberately instead.",
  );
} catch (error) {
  if (error?.code !== "ENOENT") throw error;
}

const encode = (buffer) => buffer.toString("base64url");
const password = encode(randomBytes(32));
const salt = randomBytes(24);
const iterations = 600_000;
const digest = pbkdf2Sync(password, salt, iterations, 32, "sha256");
const verifier = `pbkdf2-sha256:${iterations}:${encode(salt)}:${encode(digest)}`;
const authSecret = encode(randomBytes(32));

execFileSync(
  "/usr/bin/security",
  [
    "add-generic-password",
    "-U",
    "-a",
    "owner",
    "-s",
    "com.opensourceainewswire.hosted-dashboard.master",
    "-w",
    password,
  ],
  { stdio: ["ignore", "ignore", "pipe"] },
);

const file = await open(outputPath, "wx", 0o600);
await file.writeFile(
  [
    "AUTH_BASE_URL=http://localhost:3000",
    `AUTH_SECRET=${authSecret}`,
    `MASTER_PASSWORD_VERIFIER=${verifier}`,
    "MASTER_PASSWORD_VERSION=1",
    "# Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET after Workspace setup.",
    "",
  ].join("\n"),
  { encoding: "utf8" },
);
await file.close();

process.stdout.write(
  [
    "Local auth secrets were written to .env.local with owner-only permissions.",
    "The generated master password was saved in macOS Keychain under:",
    "Open Source AI News Wire owner (service: com.opensourceainewswire.hosted-dashboard.master)",
    "The password was not printed or written to the environment file.",
    "",
  ].join("\n"),
);
