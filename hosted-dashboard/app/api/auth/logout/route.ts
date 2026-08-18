import { NextResponse } from "next/server";
import { runtimeEnv } from "@/lib/auth/config.ts";
import {
  SESSION_COOKIE,
  assertSameOrigin,
  cookieValue,
  secureCookie,
} from "@/lib/auth/http.ts";
import { noStore } from "@/lib/auth/responses.ts";
import { audit, deleteSession, readSession } from "@/lib/auth/store.ts";

export async function POST(request: Request): Promise<NextResponse> {
  const source = runtimeEnv();
  try {
    assertSameOrigin(request, source);
    const token = cookieValue(request, SESSION_COOKIE);
    const session = await readSession(source, token);
    await deleteSession(source, token);
    if (session) {
      await audit(source, {
        actorId: session.actorId,
        role: session.role,
        action: "auth.logout",
        outcome: "success",
      });
    }
    const response = noStore(NextResponse.redirect(new URL("/", request.url), 303));
    response.cookies.set(SESSION_COOKIE, "", {
      httpOnly: true,
      secure: secureCookie(request, source),
      sameSite: "lax",
      path: "/",
      maxAge: 0,
    });
    return response;
  } catch {
    return noStore(NextResponse.json({ ok: false }, { status: 403 }));
  }
}
