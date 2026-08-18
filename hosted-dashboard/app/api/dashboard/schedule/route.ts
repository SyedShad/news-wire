import { NextResponse } from "next/server";
import { authorizeApiRequest } from "@/lib/auth/api.ts";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { noStore } from "@/lib/auth/responses.ts";
import { readSnapshot } from "@/lib/dashboard/store.ts";

export async function GET(request: Request) {
  const authorization = await authorizeApiRequest(request, ["master"]);
  if (authorization.response) return authorization.response;
  const stored = await readSnapshot(runtimeEnv().DB);
  return noStore(NextResponse.json({ ok: true, schedule: stored?.snapshot.schedule || {} }));
}
