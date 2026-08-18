import { NextResponse } from "next/server";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { SESSION_COOKIE, cookieValue } from "@/lib/auth/http.ts";
import { noStore } from "@/lib/auth/responses.ts";
import { readSession } from "@/lib/auth/store.ts";

export async function GET(request: Request): Promise<NextResponse> {
  const source = runtimeEnv();
  try {
    const session = await readSession(source, cookieValue(request, SESSION_COOKIE));
    if (!session) {
      return noStore(
        NextResponse.json({ authenticated: false }, { status: 401 }),
      );
    }
    return noStore(
      NextResponse.json({
        authenticated: true,
        actor: {
          role: session.role,
          email: session.email,
          expiresAt: session.expiresAt,
        },
      }),
    );
  } catch {
    return noStore(
      NextResponse.json({ authenticated: false }, { status: 503 }),
    );
  }
}
