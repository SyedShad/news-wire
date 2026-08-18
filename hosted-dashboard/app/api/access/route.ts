import { NextResponse } from "next/server";
import { authorizeApiRequest } from "@/lib/auth/api.ts";
import { accessProfile } from "@/lib/auth/authorization.ts";
import { noStore } from "@/lib/auth/responses.ts";

export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<NextResponse> {
  const authorization = await authorizeApiRequest(request);
  if (authorization.response) return authorization.response;

  const profile = accessProfile(authorization.session.role);
  return noStore(
    NextResponse.json({
      authenticated: true,
      actor: {
        role: authorization.session.role,
        email: authorization.session.email,
        expiresAt: authorization.session.expiresAt,
      },
      access: profile,
    }),
  );
}
