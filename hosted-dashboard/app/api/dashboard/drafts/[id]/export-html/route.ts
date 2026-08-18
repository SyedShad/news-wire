import { NextResponse } from "next/server";
import { authorizeApiRequest } from "@/lib/auth/api.ts";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { noStore } from "@/lib/auth/responses.ts";
import { readDetail, readSnapshot } from "@/lib/dashboard/store.ts";

function escape(value: unknown): string {
  return String(value ?? "").replace(/[&<>"']/gu, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;",
  }[character] || character));
}

export async function GET(request: Request, context: { params: Promise<{ id: string }> }) {
  const authorization = await authorizeApiRequest(request, ["master"]);
  if (authorization.response) return authorization.response;
  const { id } = await context.params;
  if (!/^\d+$/u.test(id)) return noStore(NextResponse.json({ error: "draft_not_found" }, { status: 404 }));
  const source = runtimeEnv();
  const stored = await readSnapshot(source.DB);
  const draft = await readDetail(source.DB, "draft", id)
    || stored?.snapshot.drafts.find((item) => String(item.id) === id && item.entry_kind === "draft");
  if (!draft) return noStore(NextResponse.json({ error: "draft_not_found" }, { status: 404 }));
  const html = `<!doctype html><html lang="en"><meta charset="utf-8"><title>${escape(draft.headline)}</title><body><main><h1>${escape(draft.headline)}</h1><p>${escape(draft.metadata)}</p><pre>${escape(draft.body)}</pre>${draft.lens ? `<h2>Open-source lens</h2><pre>${escape(draft.lens)}</pre>` : ""}</main></body></html>`;
  return noStore(new NextResponse(html, { headers: {
    "content-type": "text/html; charset=utf-8",
    "content-disposition": `attachment; filename="news-wire-draft-${id}.html"`,
  } }));
}
