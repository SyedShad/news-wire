import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { runtimeEnv } from "./config.ts";
import { SESSION_COOKIE } from "./http.ts";
import { readSession } from "./store.ts";
import type { AuthSession, Role } from "./types.ts";

export async function currentPageSession(): Promise<AuthSession | null> {
  const store = await cookies();
  return readSession(runtimeEnv(), store.get(SESSION_COOKIE)?.value);
}

export async function requirePageSession(
  roles: readonly Role[] = ["editor", "master"],
): Promise<AuthSession> {
  const session = await currentPageSession();
  if (!session) redirect("/");
  if (!roles.includes(session.role)) redirect("/");
  return session;
}
