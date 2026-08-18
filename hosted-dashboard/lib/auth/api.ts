import { NextResponse } from "next/server";
import { runtimeEnv } from "./config.ts";
import { SESSION_COOKIE, cookieValue } from "./http.ts";
import { noStore } from "./responses.ts";
import { readSession } from "./store.ts";
import type { AuthSession, Role } from "./types.ts";

type ApiAuthorization =
  | { session: AuthSession; response?: never }
  | { session?: never; response: NextResponse };

export async function authorizeApiRequest(
  request: Request,
  roles: readonly Role[] = ["editor", "master"],
): Promise<ApiAuthorization> {
  try {
    const session = await readSession(
      runtimeEnv(),
      cookieValue(request, SESSION_COOKIE),
    );
    if (!session) {
      return {
        response: noStore(
          NextResponse.json(
            { authenticated: false, error: "authentication_required" },
            { status: 401 },
          ),
        ),
      };
    }
    if (!roles.includes(session.role)) {
      return {
        response: noStore(
          NextResponse.json(
            { authenticated: true, error: "owner_access_required" },
            { status: 403 },
          ),
        ),
      };
    }
    return { session };
  } catch {
    return {
      response: noStore(
        NextResponse.json(
          { authenticated: false, error: "authorization_unavailable" },
          { status: 503 },
        ),
      ),
    };
  }
}
