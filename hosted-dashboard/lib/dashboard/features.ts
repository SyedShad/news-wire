import type { AuthRuntimeEnv } from "../auth/types.ts";

const INFERENCE_COMMANDS = new Set(["generation.create", "generation.retry", "generation.input"]);
export function generationEnabled(source: AuthRuntimeEnv): boolean {
  return source.GENERATION_ENABLED?.trim().toLowerCase() === "true";
}
export function bridgeSupportsGeneration(version: string | undefined): boolean {
  const match = /^2\.(\d+)\./u.exec(version || "");
  return Boolean(match && Number(match[1]) >= 3);
}
export function generationCommandError(operation: string, source: AuthRuntimeEnv, bridgeVersion: string | undefined): string | null {
  if (!operation.startsWith("generation.")) return null;
  if (!bridgeSupportsGeneration(bridgeVersion)) return "Upgrade the laptop runtime and bridge before using generation actions.";
  return INFERENCE_COMMANDS.has(operation) && !generationEnabled(source)
    ? "Generation setup is pending local validation. Saved work remains available."
    : null;
}
