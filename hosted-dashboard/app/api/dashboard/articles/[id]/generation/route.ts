import { NextResponse } from "next/server";
import { authorizeApiRequest } from "@/lib/auth/api.ts";
import { runtimeEnv } from "@/lib/auth/config.ts";
import { isSameOriginRequest } from "@/lib/auth/http.ts";
import { noStore } from "@/lib/auth/responses.ts";
import { listGenerationJobs, readDetail, readBridgeState, requestDetail } from "@/lib/dashboard/store.ts";
import { bridgeSupportsGeneration, generationEnabled } from "@/lib/dashboard/features.ts";
export async function GET(request: Request, context: {params: Promise<{id: string}>}) {
  const authorization = await authorizeApiRequest(request, ["master"]);
  if (authorization.response) return authorization.response;
  const {id} = await context.params;
  const source=runtimeEnv();const db=source.DB;
  const [bridge,article,catalog,jobs] = await Promise.all([readBridgeState(db),readDetail(db,"article",id),readDetail(db,"generation_catalog","runtime"),listGenerationJobs(db,id)]);
  const supportsGeneration=bridgeSupportsGeneration(bridge.version);
  if (!catalog && bridge.connected && supportsGeneration) await requestDetail(db,{resourceType:"generation_catalog",resourceId:"runtime",requestedBy:authorization.session.actorId});
  if (!article && bridge.connected && supportsGeneration) await requestDetail(db,{resourceType:"article",resourceId:id,requestedBy:authorization.session.actorId});
  return noStore(NextResponse.json({article,catalog,jobs,connected:bridge.connected,enabled:generationEnabled(source),supportsGeneration}));
}

export async function POST(request: Request) {
  const authorization=await authorizeApiRequest(request,["master"]);
  if(authorization.response) return authorization.response;
  const source=runtimeEnv();
  if(!isSameOriginRequest(request,source)) return noStore(NextResponse.json({error:"same_origin_required"},{status:403}));
  const bridge=await readBridgeState(source.DB);
  if(!bridge.connected) return noStore(NextResponse.json({error:"laptop_offline"},{status:409}));
  if(!bridgeSupportsGeneration(bridge.version)) return noStore(NextResponse.json({error:"Upgrade the laptop runtime and bridge to refresh generation catalogs."},{status:409}));
  await requestDetail(source.DB,{resourceType:"generation_catalog",resourceId:"runtime",requestedBy:authorization.session.actorId});
  return noStore(NextResponse.json({ok:true,pending:true},{status:202}));
}
