import { NextResponse } from "next/server";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { noStore } from "@/lib/auth/responses.ts";
import { verifyBridgeRequest } from "@/lib/bridge/auth.ts";
import { bridgeError } from "@/lib/bridge/responses.ts";
import { claimCommands, claimReadRequests, recordBridgeHeartbeat } from "@/lib/dashboard/store.ts";

export async function GET(request: Request) {
  try {
    const source = runtimeEnv();
    await verifyBridgeRequest(request, source);
    const bridgeVersion = request.headers.get("x-news-wire-bridge-version")?.trim() || "unknown";
    const runtimeVersion = request.headers.get("x-news-wire-runtime-version")?.trim() || "unknown";
    await recordBridgeHeartbeat(source.DB, {
      bridgeVersion: bridgeVersion.slice(0, 80),
      runtimeVersion: runtimeVersion.slice(0, 80),
    });
    const commands = await claimCommands(source.DB, 10);
    const readRequests = await claimReadRequests(source.DB, 10);
    return noStore(NextResponse.json({ ok: true, commands, read_requests: readRequests }));
  } catch (error) {
    return bridgeError(error);
  }
}
