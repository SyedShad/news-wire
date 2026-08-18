import { NextResponse } from "next/server";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { noStore } from "@/lib/auth/responses.ts";
import { verifyBridgeRequest } from "@/lib/bridge/auth.ts";
import { bridgeError } from "@/lib/bridge/responses.ts";
import {
  completeResourceSync,
  completeProjectionSync,
  saveResourceChunk,
  saveProjectionState,
  saveStoryChunk,
} from "@/lib/dashboard/store.ts";
import { MAX_SYNC_CHUNK_BYTES, parseBridgeSync } from "@/lib/dashboard/validation.ts";

export async function PUT(request: Request) {
  try {
    const source = runtimeEnv();
    const verified = await verifyBridgeRequest(request, source);
    if (verified.body.byteLength > MAX_SYNC_CHUNK_BYTES) {
      return noStore(NextResponse.json({ ok: false, error: "sync_chunk_too_large" }, { status: 413 }));
    }
    const envelope = parseBridgeSync(JSON.parse(verified.bodyText));
    const bridgeVersion = request.headers.get("x-news-wire-bridge-version")?.trim() || "unknown";
    if (!bridgeVersion.startsWith("2.")) {
      return noStore(NextResponse.json({ ok: false, error: "bridge_version_unsupported" }, { status: 426 }));
    }
    if (envelope.kind === "state") {
      const result = await saveProjectionState(source.DB, {
        syncId: envelope.sync_id,
        storyDigest: envelope.story_digest,
        storyTotal: envelope.story_total,
        resourceDigest: envelope.resource_digest,
        resourceTotal: envelope.resource_total,
        snapshot: envelope.snapshot,
        bridgeVersion: bridgeVersion.slice(0, 80),
      });
      return noStore(NextResponse.json({
        ok: true,
        stories_required: result.storiesRequired,
        resources_required: result.resourcesRequired,
        story_digest: result.storyDigest,
        resource_digest: result.resourceDigest,
      }));
    }
    if (envelope.kind === "stories") {
      await saveStoryChunk(
        source.DB,
        envelope.sync_id,
        envelope.mode,
        envelope.stories,
        envelope.deleted_ids,
      );
      return noStore(NextResponse.json({ ok: true, accepted: envelope.stories.length }));
    }
    if (envelope.kind === "resources") {
      await saveResourceChunk(
        source.DB,
        envelope.sync_id,
        envelope.mode,
        envelope.resources,
        envelope.deleted_ids,
      );
      return noStore(NextResponse.json({ ok: true, accepted: envelope.resources.length }));
    }
    if (envelope.projection === "stories") {
      await completeProjectionSync(
        source.DB,
        envelope.sync_id,
        envelope.digest,
        envelope.total,
        envelope.mode,
      );
    } else {
      await completeResourceSync(
        source.DB,
        envelope.sync_id,
        envelope.digest,
        envelope.total,
        envelope.mode,
      );
    }
    return noStore(NextResponse.json({ ok: true, synchronized: envelope.total, projection: envelope.projection }));
  } catch (error) {
    if (error instanceof SyntaxError || error instanceof TypeError) {
      return noStore(NextResponse.json({ ok: false, error: "sync_invalid" }, { status: 400 }));
    }
    if (error instanceof Error && new Set(["story_projection_incomplete", "resource_projection_incomplete"]).has(error.message)) {
      return noStore(NextResponse.json({ ok: false, error: error.message }, { status: 409 }));
    }
    return bridgeError(error);
  }
}
