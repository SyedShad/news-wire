import { NextResponse } from "next/server";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { noStore } from "@/lib/auth/responses.ts";
import { verifyBridgeRequest } from "@/lib/bridge/auth.ts";
import { bridgeError } from "@/lib/bridge/responses.ts";
import { completeReadRequest } from "@/lib/dashboard/store.ts";
import { isRecord } from "@/lib/dashboard/validation.ts";
import type { JsonValue } from "@/lib/dashboard/types.ts";

export async function POST(
  request: Request,
  context: { params: Promise<{ id: string }> },
) {
  try {
    const source = runtimeEnv();
    const verified = await verifyBridgeRequest(request, source);
    const value: unknown = JSON.parse(verified.bodyText);
    if (!isRecord(value) || typeof value.ok !== "boolean") {
      return noStore(NextResponse.json({ ok: false, error: "read_result_invalid" }, { status: 400 }));
    }
    const { id } = await context.params;
    if (!/^[0-9a-f-]{36}$/iu.test(id)) {
      return noStore(NextResponse.json({ ok: false, error: "read_request_id_invalid" }, { status: 400 }));
    }
    const payload = isRecord(value.payload)
      ? value.payload as Record<string, JsonValue>
      : null;
    const updated = await completeReadRequest(source.DB, id, {
      ok: value.ok,
      payload,
      error: typeof value.error === "string" ? value.error : null,
    });
    if (!updated) {
      return noStore(NextResponse.json({ ok: false, error: "read_request_not_claimed" }, { status: 409 }));
    }
    return noStore(NextResponse.json({ ok: true }));
  } catch (error) {
    if (error instanceof SyntaxError) {
      return noStore(NextResponse.json({ ok: false, error: "read_result_invalid" }, { status: 400 }));
    }
    return bridgeError(error);
  }
}
