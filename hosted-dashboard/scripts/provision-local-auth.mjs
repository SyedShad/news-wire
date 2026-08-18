import { pbkdf2Sync, randomBytes } from "node:crypto";
import { execFileSync } from "node:child_process";
import { open, readFile, rename, stat, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const outputPath = resolve(".env.local");
const encode = (buffer) => buffer.toString("base64url");
const iterations = 100_000;
const keychainAccount = "owner";
const keychainService = "com.opensourceainewswire.hosted-dashboard.master";

function createVerifier(password) {
  const salt = randomBytes(24);
  const digest = pbkdf2Sync(password, salt, iterations, 32, "sha256");
  return `pbkdf2-sha256:${iterations}:${encode(salt)}:${encode(digest)}`;
}

async function refreshVerifier() {
  const password = execFileSync(
    "/usr/bin/security",
    ["find-generic-password", "-w", "-a", keychainAccount, "-s", keychainService],
    { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] },
  ).trim();
  if (!password) throw new Error("The owner password is missing from macOS Keychain.");

  const current = await readFile(outputPath, "utf8");
  if (!/^MASTER_PASSWORD_VERIFIER=.+$/mu.test(current)) {
    throw new Error(".env.local does not contain MASTER_PASSWORD_VERIFIER.");
  }
  const updated = current.replace(
    /^MASTER_PASSWORD_VERIFIER=.+$/mu,
    `MASTER_PASSWORD_VERIFIER=${createVerifier(password)}`,
  );
  const temporaryPath = `${outputPath}.tmp-${process.pid}`;
  await writeFile(temporaryPath, updated, { encoding: "utf8", flag: "wx", mode: 0o600 });
  await rename(temporaryPath, outputPath);
  process.stdout.write(
    "Refreshed the local verifier for the existing Keychain owner password. No credential was printed.\n",
  );
}

async function provision() {
  try {
    await stat(outputPath);
    throw new Error(
      ".env.local already exists. Refusing to overwrite it; rotate credentials deliberately instead.",
    );
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }

  const password = encode(randomBytes(32));
  const verifier = createVerifier(password);
  const authSecret = encode(randomBytes(32));

  execFileSync(
    "/usr/bin/security",
    [
      "add-generic-password",
      "-U",
      "-a",
      keychainAccount,
      "-s",
      keychainService,
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
}

if (process.argv.includes("--refresh-verifier")) {
  await refreshVerifier();
} else {
  await provision();
}
