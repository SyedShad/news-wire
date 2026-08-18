import { NextResponse } from "next/server";
import { authorizeApiRequest } from "@/lib/auth/api.ts";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { noStore } from "@/lib/auth/responses.ts";
import { readDetail, readSnapshot } from "@/lib/dashboard/store.ts";

function markdown(draft: Record<string, unknown>): string {
  if (draft.mode === "Reddit Post") {
    return [
      `**Title:** ${String(draft.headline || "")}`,
      "",
      String(draft.body || ""),
      "",
      `**Suggested flair:** ${String(draft.suggested_flair || "Not specified")}`,
      "",
      "Verify rules before posting",
      "",
    ].join("\n");
  }
  return [
    `# ${String(draft.headline || "")}`,
    "",
    String(draft.metadata || ""),
    "",
    String(draft.body || ""),
    draft.lens ? `\n${String(draft.lens)}\n` : "",
  ].join("\n");
}

export async function GET(
  request: Request,
  context: { params: Promise<{ id: string }> },
) {
  const authorization = await authorizeApiRequest(request);
  if (authorization.response) return authorization.response;
  const { id } = await context.params;
  if (!/^\d+$/u.test(id)) {
    return noStore(NextResponse.json({ error: "draft_not_found" }, { status: 404 }));
  }
  const source = runtimeEnv();
  const stored = await readSnapshot(source.DB);
  const draft = await readDetail(source.DB, "draft", id)
    || stored?.snapshot.drafts.find((item) => String(item.id) === id && item.entry_kind === "draft");
  if (!draft) return noStore(NextResponse.json({ error: "draft_not_found" }, { status: 404 }));
  return noStore(new NextResponse(markdown(draft), {
    headers: {
      "content-type": "text/markdown; charset=utf-8",
      "content-disposition": `attachment; filename="news-wire-draft-${id}.md"`,
    },
  }));
}
