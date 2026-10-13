import { NextResponse } from "next/server";
import { authorizeApiRequest } from "@/lib/auth/api.ts";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { noStore } from "@/lib/auth/responses.ts";
import { readResource } from "@/lib/dashboard/store.ts";
import { assembleArtifact, artifactDisposition, verifyArtifactDigest } from "@/lib/dashboard/artifacts.ts";
export async function GET(request: Request, context: {params: Promise<{id: string}>}) {
  const authorization=await authorizeApiRequest(request,["master"]);
  if(authorization.response) return authorization.response;
  const {id}=await context.params;
  if(!id || id.length>240) return noStore(NextResponse.json({error:"artifact_id_invalid"},{status:400}));
  const db=runtimeEnv().DB;
  const metadata=await readResource(db,"generation_artifact",id);
  if(!metadata) return noStore(NextResponse.json({error:"artifact_not_synced"},{status:404}));
  const count=Number(metadata.chunk_count);
  if(!Number.isSafeInteger(count) || count<1 || count>52) return noStore(NextResponse.json({error:"artifact_metadata_invalid"},{status:422}));
  const chunks=await Promise.all(Array.from({length:count},(_,index) => readResource(db,"generation_artifact_chunk",`${id}:${index}`)));
  if(chunks.some((chunk) => !chunk)) return noStore(NextResponse.json({error:"artifact_sync_pending"},{status:503}));
  try {
    const data=assembleArtifact(metadata,chunks.filter((chunk) => chunk !== null));
    await verifyArtifactDigest(metadata,data);
    const mime=typeof metadata.mime_type === "string" && /^[a-z0-9][a-z0-9.+-]*\/[a-z0-9][a-z0-9.+-]*$/iu.test(metadata.mime_type) ? metadata.mime_type : "application/octet-stream";
    const name=(typeof metadata.name === "string" ? metadata.name : "artifact").replace(/[\r\n\x00]/gu,"").split(/[\/\\]/u).pop() || "artifact";
    const disposition=artifactDisposition(mime,new URL(request.url).searchParams.get("download") === "1");
    return new Response(data,{headers:{"content-type":mime,"content-length":String(data.length),"content-disposition":`${disposition}; filename*=UTF-8''${encodeURIComponent(name)}`,"cache-control":"private, no-store","x-content-type-options":"nosniff","content-security-policy":"sandbox; default-src 'none'"}});
  } catch {return noStore(NextResponse.json({error:"artifact_invalid"},{status:422}));}
}
