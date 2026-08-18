import { NextResponse } from "next/server";
import { authorizeApiRequest } from "@/lib/auth/api.ts";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { noStore } from "@/lib/auth/responses.ts";
import { listCommands, readSnapshot } from "@/lib/dashboard/store.ts";

export async function GET(request: Request) {
  const authorization = await authorizeApiRequest(request, ["master"]);
  if (authorization.response) return authorization.response;
  const source = runtimeEnv();
  const stored = await readSnapshot(source.DB);
  const commands = authorization.session.role === "master"
    ? await listCommands(source.DB, 30)
    : undefined;
  return noStore(NextResponse.json({
    ok: true,
    connected: Boolean(stored),
    data: stored,
    commands,
  }));
}
