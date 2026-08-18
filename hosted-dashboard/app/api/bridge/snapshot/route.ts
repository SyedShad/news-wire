import { NextResponse } from "next/server";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { noStore } from "@/lib/auth/responses.ts";
import { verifyBridgeRequest } from "@/lib/bridge/auth.ts";
import { bridgeError } from "@/lib/bridge/responses.ts";

export async function PUT(request: Request) {
  try {
    const source = runtimeEnv();
    const verified = await verifyBridgeRequest(request, source);
    void verified;
    return noStore(NextResponse.json({ ok: false, error: "snapshot_protocol_retired" }, { status: 410 }));
  } catch (error) {
    return bridgeError(error);
  }
}
