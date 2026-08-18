import { NextResponse } from "next/server";
import { authorizeApiRequest } from "@/lib/auth/api.ts";
import { accessProfile } from "@/lib/auth/authorization.ts";
import { noStore } from "@/lib/auth/responses.ts";

export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<NextResponse> {
  const authorization = await authorizeApiRequest(request, ["master"]);
  if (authorization.response) return authorization.response;

  return noStore(
    NextResponse.json({
      authenticated: true,
      owner: true,
      access: accessProfile(authorization.session.role),
    }),
  );
}
