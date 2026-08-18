import { env } from "cloudflare:workers";
import type { AuthRuntimeEnv } from "./types.ts";

export class AuthConfigurationError extends Error {}

export function runtimeEnv(): AuthRuntimeEnv {
  return env as unknown as AuthRuntimeEnv;
}

export function requireEnv(
  source: AuthRuntimeEnv,
  key:
    | "AUTH_SECRET"
    | "GOOGLE_CLIENT_ID"
    | "GOOGLE_CLIENT_SECRET"
    | "MASTER_PASSWORD_VERIFIER",
): string {
  const value = source[key]?.trim();
  if (!value) throw new AuthConfigurationError(`${key} is not configured`);
  return value;
}

export function authBaseUrl(request: Request, source: AuthRuntimeEnv): URL {
  const configured = source.AUTH_BASE_URL?.trim();
  const base = new URL(configured || new URL(request.url).origin);
  if (base.pathname !== "/" || base.search || base.hash) {
    throw new AuthConfigurationError("AUTH_BASE_URL must contain only an origin");
  }
  if (base.protocol !== "https:" && base.hostname !== "localhost") {
    throw new AuthConfigurationError(
      "AUTH_BASE_URL must use HTTPS except for localhost development",
    );
  }
  return base;
}

export function masterPasswordVersion(source: AuthRuntimeEnv): string {
  return source.MASTER_PASSWORD_VERSION?.trim() || "1";
}
