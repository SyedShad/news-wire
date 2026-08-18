import { lstat, mkdir, readlink, symlink } from "node:fs/promises";
import { spawn } from "node:child_process";
import path from "node:path";
import process from "node:process";

const root = process.cwd();
const localEnvironment = path.join(root, ".env.local");
const serverDirectory = path.join(root, "dist", "server");
const localEnvironmentLink = path.join(serverDirectory, ".dev.vars");
const expectedLinkTarget = "../../.env.local";

async function ensureLocalEnvironmentLink() {
  await lstat(localEnvironment).catch(() => {
    throw new Error(".env.local is missing; run the local auth provisioning commands first.");
  });
  await mkdir(serverDirectory, { recursive: true });

  try {
    const metadata = await lstat(localEnvironmentLink);
    if (!metadata.isSymbolicLink() || (await readlink(localEnvironmentLink)) !== expectedLinkTarget) {
      throw new Error("dist/server/.dev.vars exists but is not the expected local-only environment link.");
    }
  } catch (error) {
    if (error?.code !== "ENOENT") {
      throw error;
    }
    await symlink(expectedLinkTarget, localEnvironmentLink);
  }
}

await ensureLocalEnvironmentLink();

const wrangler = path.join(root, "node_modules", ".bin", "wrangler");
const child = spawn(
  wrangler,
  [
    "dev",
    "--config",
    "dist/server/wrangler.json",
    "--persist-to",
    ".wrangler/state",
    "--port",
    process.env.PORT || "3000",
    "--show-interactive-dev-session",
    "false",
  ],
  {
    cwd: root,
    env: {
      ...process.env,
      WRANGLER_LOG_PATH: process.env.WRANGLER_LOG_PATH || ".wrangler/wrangler.log",
    },
    stdio: "inherit",
  },
);

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => child.kill(signal));
}

child.on("error", (error) => {
  console.error(error.message);
  process.exitCode = 1;
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exitCode = code ?? 1;
});
