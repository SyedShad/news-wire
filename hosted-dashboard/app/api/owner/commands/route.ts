import { NextResponse } from "next/server";
import { authorizeApiRequest } from "@/lib/auth/api.ts";
import { audit } from "@/lib/auth/store.ts";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { isSameOriginRequest } from "@/lib/auth/http.ts";
import { noStore } from "@/lib/auth/responses.ts";
import { enqueueCommand, listCommands, readSnapshot } from "@/lib/dashboard/store.ts";
import { validateOwnerCommand } from "@/lib/dashboard/validation.ts";

export async function GET(request: Request) {
  const authorization = await authorizeApiRequest(request, ["master"]);
  if (authorization.response) return authorization.response;
  return noStore(NextResponse.json({ ok: true, commands: await listCommands(runtimeEnv().DB, 50) }));
}

export async function POST(request: Request) {
  const authorization = await authorizeApiRequest(request, ["master"]);
  if (authorization.response) return authorization.response;
  if (!isSameOriginRequest(request, runtimeEnv())) {
    return noStore(NextResponse.json({ ok: false, error: "same_origin_required" }, { status: 403 }));
  }
  try {
    const source = runtimeEnv();
    const stored = await readSnapshot(source.DB);
    if (!stored?.bridgeConnected) {
      return noStore(NextResponse.json({
        ok: false,
        error: "The laptop bridge is offline. No command was queued.",
      }, { status: 409 }));
    }
    const input = validateOwnerCommand(await request.json());
    const command = await enqueueCommand(source.DB, {
      ...input,
      requestedBy: authorization.session.actorId,
    });
    await audit(source, {
      actorId: authorization.session.actorId,
      role: authorization.session.role,
      action: "owner_command_queued",
      outcome: "success",
      detail: { command_id: command.id, operation: command.operation },
    });
    return noStore(NextResponse.json({ ok: true, command }, { status: 202 }));
  } catch (error) {
    const message = error instanceof Error ? error.message : "command_invalid";
    return noStore(NextResponse.json({ ok: false, error: message }, { status: 400 }));
  }
}
