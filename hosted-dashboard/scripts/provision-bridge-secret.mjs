import { randomBytes } from "node:crypto";
import { execFileSync } from "node:child_process";
import { open, readFile } from "node:fs/promises";
import { resolve } from "node:path";

const outputPath = resolve(".env.local");
const existing = await readFile(outputPath, "utf8");
if (/^BRIDGE_SECRET=/mu.test(existing)) {
  throw new Error("BRIDGE_SECRET already exists. Refusing to rotate it implicitly.");
}

const productionHost = process.argv[2]?.trim();
if (!productionHost || !/^[a-z0-9.-]+$/iu.test(productionHost)) {
  throw new Error("Provide the production site hostname as the only argument.");
}

const secret = randomBytes(48).toString("base64url");
for (const account of ["localhost:3000", productionHost.toLowerCase()]) {
  execFileSync(
    "/usr/bin/security",
    [
      "add-generic-password",
      "-U",
      "-a",
      account,
      "-s",
      "com.opensourceainewswire.hostedbridge.secret",
      "-w",
      secret,
    ],
    { stdio: ["ignore", "ignore", "pipe"] },
  );
}

const file = await open(outputPath, "a", 0o600);
await file.writeFile(`BRIDGE_SECRET=${secret}\n`, { encoding: "utf8" });
await file.close();

process.stdout.write(
  [
    "A bridge secret was added to the ignored local environment and macOS Keychain.",
    "The secret was not printed. Separate Keychain entries cover localhost and the production site.",
    "",
  ].join("\n"),
);
