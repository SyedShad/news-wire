import assert from "node:assert/strict";
import { createHash, createHmac } from "node:crypto";
import { readFile } from "node:fs/promises";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";
import { createSession } from "../lib/auth/store.ts";
import { artifactDisposition, assembleArtifact, MAX_ARTIFACT_BYTES, verifyArtifactDigest } from "../lib/dashboard/artifacts.ts";
import { claimCommands, claimReadRequests, completeReadRequest, listGenerationJobs, listStoryArticles, listStoryPage, readDetail, requestDetail, saveProjectionState, saveResourceChunk, saveStoryChunk } from "../lib/dashboard/store.ts";
import { parseBridgeSync, validateOwnerCommand } from "../lib/dashboard/validation.ts";
import { bridgeSupportsGeneration, generationCommandError, generationEnabled } from "../lib/dashboard/features.ts";
class Statement {
  constructor(database, sql) {this.database=database;this.sql=sql;this.values=[];}
  bind(...values) {this.values=values;return this;}
  async run() {const result=this.database.sqlite.prepare(this.sql).run(...this.values);return {success:true,meta:{changes:Number(result.changes)}};}
  async first() {return this.database.sqlite.prepare(this.sql).get(...this.values) || null;}
  async all() {return {success:true,results:this.database.sqlite.prepare(this.sql).all(...this.values)};}
}
class SqliteD1 {
  constructor() {this.sqlite=new DatabaseSync(":memory:");}
  prepare(sql) {return new Statement(this,sql);}
  async batch(statements) {const results=[];for(const statement of statements) results.push(await statement.run());return results;}
}
const snapshot={schema_version:1,generated_at:"2026-10-13T12:00:00Z",runtime_version:"0.5.0",overview:{counts:{},sources:{},queue_count:0,queue_lag:"Clear",top_stories:[],alerts:[],scans:[]},stories:[],drafts:[],sources:[],schedule:{},notices:[],diagnostics:[]};
const syncId="1234567890abcdef";
async function populatedDatabase() {
  const db=new SqliteD1();
  await saveProjectionState(db,{syncId,storyDigest:"a".repeat(64),storyTotal:3,resourceDigest:"b".repeat(64),resourceTotal:0,snapshot,bridgeVersion:"2.3.0"});
  await saveStoryChunk(db,syncId,"full",[
    {id:"article-old",headline:"Previously published story",status:"fetched",first_fetched_at:"2026-10-13T09:00:00Z",published_at:"2024-01-01T00:00:00Z",review_score:0,is_review_current:false,priority_rank:2},
    {id:"article-new",headline:"Newest fetched story",status:"fetched",first_fetched_at:"2026-10-13T10:00:00Z",published_at:null,review_score:0,is_review_current:false,priority_rank:1},
    {id:"article-archived",headline:"Human archived story",status:"archived",first_fetched_at:"2026-10-12T10:00:00Z",review_score:100,is_review_current:true,priority_rank:0},
  ],[]);
  return db;
}
test("article feed includes older and low-score items and follows fetch chronology with pagination",async () => {
  const db=await populatedDatabase();
  const first=await listStoryPage(db,{window:"review_now",sort:"priority",kind:"ready",pageSize:2});
  assert.equal(first.total,3);assert.deepEqual(first.stories.map((article) => article.id),["article-new","article-old"]);
  const second=await listStoryPage(db,{cursor:first.nextCursor,pageSize:2});
  assert.deepEqual(second.stories.map((article) => article.id),["article-archived"]);
});
test("article and generation resources round-trip and job lists are scoped to each article",async () => {
  const db=await populatedDatabase();
  await saveResourceChunk(db,syncId,"full",[
    {resource_type:"article_detail",resource_id:"article-old",rank:0,payload:{id:"article-old",headline:"Old article"}},
    {resource_type:"generation_catalog",resource_id:"runtime",rank:0,payload:{models:[{id:"model-1"}],skills:[{id:"skill-1"}]}},
    {resource_type:"generation_job",resource_id:"job-1",rank:0,payload:{id:"job-1",article_id:"article-old",task_type:"text",status:"completed",output_text:"Draft"}},
    {resource_type:"generation_job",resource_id:"job-2",rank:1,payload:{id:"job-2",article_id:"article-new",task_type:"graphics",status:"queued"}},
  ],[]);
  assert.equal((await readDetail(db,"article","article-old")).headline,"Old article");
  assert.equal((await readDetail(db,"generation_catalog","runtime")).models[0].id,"model-1");
  assert.deepEqual((await listGenerationJobs(db,"article-old")).map((job) => job.id),["job-1"]);
  await db.prepare("INSERT INTO command_queue (id,operation,payload_json,status,requested_by,requested_role,created_at,expires_at) VALUES ('retired','article_alert.start_research','{}','pending','master','master',?,?)").bind(Date.now(),Date.now()+60000).run();
  assert.equal((await claimCommands(db)).length,0);
  assert.equal((await db.prepare("SELECT status FROM command_queue WHERE id='retired'").first()).status,"failed");
  const request=await requestDetail(db,{resourceType:"generation_job",resourceId:"job-missing",requestedBy:"master"});
  assert.equal((await claimReadRequests(db))[0].resourceType,"generation_job");
  await completeReadRequest(db,request.id,{ok:true,payload:{id:"job-missing",status:"completed"}});
  assert.equal((await readDetail(db,"generation_job","job-missing")).status,"completed");
  const catalogRequest=await requestDetail(db,{resourceType:"generation_catalog",resourceId:"runtime",requestedBy:"master"});
  await claimReadRequests(db);
  await completeReadRequest(db,catalogRequest.id,{ok:true,payload:{ready:true,models:[{id:"refreshed-model"}]}});
  await db.prepare("UPDATE detail_cache SET updated_at=updated_at+1000 WHERE resource_key='generation_catalog:runtime'").run();
  assert.equal((await readDetail(db,"generation_catalog","runtime")).models[0].id,"refreshed-model");
});
test("generation validation keeps ordered skills, structured input and editable output",() => {
  const create={operation:"generation.create",payload:{article_id:"article-1",task_type:"graphics",model:"model-1",skill_ids:["skill-b","skill-a"],customizations:"Square graphic",request_id:"request-1"}};
  assert.deepEqual(validateOwnerCommand(create).payload.skill_ids,["skill-b","skill-a"]);
  assert.throws(() => validateOwnerCommand({...create,payload:{...create.payload,skill_ids:[]}}),/one or more skills/);
  assert.throws(() => validateOwnerCommand({...create,payload:{...create.payload,skill_ids:["a","a"]}}),/Duplicate skills/);
  assert.throws(() => validateOwnerCommand({...create,payload:{...create.payload,task_type:"publish"}}),/Unsupported generation task/);
  assert.deepEqual(validateOwnerCommand({operation:"generation.input",payload:{job_id:"job-1",answers:{decision:"Decline"}}}).payload.answers,{decision:"Decline"});
  assert.throws(() => validateOwnerCommand({operation:"generation.input",payload:{job_id:"job-1",answers:"yes"}}),/answers must be an object/);
  assert.equal(validateOwnerCommand({operation:"generation.save",payload:{job_id:"job-1",output_text:"x".repeat(40000)}}).payload.output_text.length,40000);
  assert.equal(validateOwnerCommand({operation:"operations.mark_read",payload:{}}).operation,"operations.mark_read");
  for (const operation of ["story.evidence.inspect","story.evidence.confirm","story.evidence.exclude","story.content.create","story.draft.retry"]) assert.throws(() => validateOwnerCommand({operation,payload:{story_id:"old"}}),/Unsupported owner operation/);
  assert.equal(parseBridgeSync({schema_version:2,kind:"resources",sync_id:syncId,resources:[{resource_type:"generation_artifact_chunk",resource_id:"job:asset:0",rank:0,payload:{index:0,data_base64:"YWJj"}}]}).resources[0].resource_type,"generation_artifact_chunk");
});
test("inference is disabled by default while non-inference work remains available on the upgraded bridge",() => {
  assert.equal(generationEnabled({}),false);assert.equal(generationEnabled({GENERATION_ENABLED:"true"}),true);
  assert.equal(bridgeSupportsGeneration("2.2.0"),false);assert.equal(bridgeSupportsGeneration("2.3.0"),true);
  for(const operation of ["generation.create","generation.retry","generation.input"]) assert.match(generationCommandError(operation,{},"2.3.0"),/pending local validation/);
  for(const operation of ["generation.cancel","generation.save"]) assert.equal(generationCommandError(operation,{},"2.3.0"),null);
  assert.equal(generationCommandError("generation.create",{GENERATION_ENABLED:"true"},"2.3.0"),null);
  assert.match(generationCommandError("generation.create",{GENERATION_ENABLED:"true"},"2.2.0"),/Upgrade/);
});
test("artifact assembly validates order, bounds, size and SHA256 before previewing",async () => {
  const bytes=Buffer.from("authenticated artifact");
  const metadata={size_bytes:bytes.length,chunk_count:2,sha256:createHash("sha256").update(bytes).digest("hex")};
  const chunks=[{index:0,data_base64:bytes.subarray(0,5).toString("base64")},{index:1,data_base64:bytes.subarray(5).toString("base64")}];
  const data=assembleArtifact(metadata,chunks);assert.equal(Buffer.from(data).toString(),bytes.toString());await verifyArtifactDigest(metadata,data);
  assert.throws(() => assembleArtifact(metadata,[chunks[1],chunks[0]]),/chunk_invalid/);
  assert.throws(() => assembleArtifact({...metadata,size_bytes:MAX_ARTIFACT_BYTES+1},chunks),/incomplete/);
  assert.throws(() => assembleArtifact({...metadata,size_bytes:1},chunks),/size_invalid/);
  await assert.rejects(verifyArtifactDigest({...metadata,sha256:"0".repeat(64)},data),/digest_mismatch/);
  const fullChunk=Buffer.alloc(500*1024,7);
  const fullMetadata={size_bytes:fullChunk.length,chunk_count:1,sha256:createHash("sha256").update(fullChunk).digest("hex")};
  await verifyArtifactDigest(fullMetadata,assembleArtifact(fullMetadata,[{index:0,data_base64:fullChunk.toString("base64")}]));
  assert.equal(artifactDisposition("image/png",false),"inline");assert.equal(artifactDisposition("image/svg+xml",false),"attachment");assert.equal(artifactDisposition("text/html",false),"attachment");
});
test("migration preserves historical requests and permits generation read requests",async () => {
  const db=new DatabaseSync(":memory:");
  db.exec("CREATE TABLE command_queue (id TEXT, created_at INTEGER);"+await readFile(new URL("../drizzle/0004_rare_ken_ellis.sql",import.meta.url),"utf8"));
  db.exec("INSERT INTO read_request (id,resource_type,resource_id,status,requested_by,created_at,expires_at) VALUES ('legacy','story','old','pending','master',1,2)");
  db.exec(await readFile(new URL("../drizzle/0007_article_generation.sql",import.meta.url),"utf8"));
  assert.equal(db.prepare("SELECT resource_type FROM read_request WHERE id='legacy'").get().resource_type,"story");
  db.exec("INSERT INTO read_request (id,resource_type,resource_id,status,requested_by,created_at,expires_at) VALUES ('new','generation_catalog','runtime','pending','master',1,2)");
  assert.throws(() => db.exec("INSERT INTO read_request (id,resource_type,resource_id,status,requested_by,created_at,expires_at) VALUES ('bad','shell','x','pending','master',1,2)"),/CHECK/);
});
const workerUrl=new URL("../dist/server/index.js",import.meta.url);workerUrl.searchParams.set("test",`${process.pid}-${Date.now()}`);
const {default:worker}=await import(workerUrl.href);
const context={waitUntil(){},passThroughOnException(){}};
test("workspace and artifact routes are owner-only and serve verified protected outputs",async () => {
  const db=await populatedDatabase();
  const environment={DB:db,ASSETS:{fetch:async () => new Response("Not found",{status:404})},CONTENT:{},AUTH_BASE_URL:"http://localhost",AUTH_SECRET:"test-auth-secret-long-enough-for-tests",MASTER_PASSWORD_VERSION:"1"};
  await saveProjectionState(db,{syncId,storyDigest:"a".repeat(64),storyTotal:3,resourceDigest:"b".repeat(64),resourceTotal:0,snapshot:{...snapshot,notices:[{id:"generation-unread",kind:"generation",title:"Generation needs attention",read_at:null},{id:"generation-read",kind:"generation",title:"Earlier generation notice",read_at:"2026-10-13T12:00:00Z"}]},bridgeVersion:"2.3.0"});
  const token=await createSession(environment,{actorId:"master",role:"master",email:null,credentialVersion:"1",lifetimeSeconds:60});
  const bytes=Buffer.from("image artifact bytes");
  const metadata={id:"job-1:asset-1",job_id:"job-1",artifact_id:"asset-1",name:"cover.png",mime_type:"image/png",size_bytes:bytes.length,chunk_count:1,sha256:createHash("sha256").update(bytes).digest("hex")};
  await saveResourceChunk(db,syncId,"full",[
    {resource_type:"article_detail",resource_id:"article-new",rank:0,payload:{id:"article-new",headline:"An article",source_name:"Publisher",canonical_url:"https://example.com/news",status:"fetched",summary:"Source supplied context."}},
    {resource_type:"generation_catalog",resource_id:"runtime",rank:0,payload:{models:[{id:"model-1",display_name:"Runtime model"}],skills:[{id:"skill-1",name:"Editorial writing"}],default_model:"model-1",ready:false}},
    {resource_type:"generation_job",resource_id:"job-1",rank:0,payload:{id:"job-1",article_id:"article-new",task_type:"text",model:"model-1",status:"completed",output_text:"Saved content"}},
    {resource_type:"generation_artifact",resource_id:metadata.id,rank:0,payload:metadata},
    {resource_type:"generation_artifact_chunk",resource_id:`${metadata.id}:0`,rank:0,payload:{index:0,data_base64:bytes.toString("base64")}},
  ],[]);
  const fetchApp=(path,authenticated=false) => {globalThis.__CLOUDFLARE_TEST_ENV__=environment;return worker.fetch(new Request(`http://localhost${path}`,{headers:authenticated ? {cookie:`osainw_session=${token}`} : {},redirect:"manual"}),environment,context);};
  assert.equal((await fetchApp("/api/dashboard/articles/article-new/generation")).status,401);
  assert.equal((await fetchApp("/api/dashboard/generation/artifacts/job-1%3Aasset-1")).status,401);
  const artifact=await fetchApp("/api/dashboard/generation/artifacts/job-1%3Aasset-1",true);
  assert.equal(artifact.status,200);assert.equal(Buffer.from(await artifact.arrayBuffer()).toString(),bytes.toString());
  assert.match(artifact.headers.get("content-disposition"),/^inline/);assert.match(artifact.headers.get("content-security-policy"),/sandbox/);
  const response=await fetchApp("/dashboard/articles/article-new",true);assert.equal(response.status,200);
  const html=await response.text();assert.match(html,/Generate content/);assert.match(html,/Generate graphics/);assert.match(html,/Runtime model/);assert.match(html,/Editorial writing/);assert.doesNotMatch(html,/Review score|Organic score|Priority floor|Trusted source|Confirm evidence/);
  assert.match(html,/generation login/);assert.match(html,/Download text/);
  const operations=await fetchApp("/dashboard/operations",true);assert.equal(operations.status,200);
  const operationHtml=await operations.text();assert.match(operationHtml,/Generation needs attention/);assert.match(operationHtml.replace(/<!--[\s\S]*?-->/gu,""),/1 unread/);assert.match(operationHtml,/nav-count[^>]*>1</);
  assert.equal((await fetchApp("/api/dashboard/generation/artifacts/job-unknown%3Aasset-1",true)).status,404);
  const command=async (operation,payload) => {globalThis.__CLOUDFLARE_TEST_ENV__=environment;return worker.fetch(new Request("http://localhost/api/owner/commands",{method:"POST",headers:{cookie:`osainw_session=${token}`,origin:"http://localhost","content-type":"application/json"},body:JSON.stringify({operation,payload})}),environment,context);};
  for(const [operation,payload] of [["generation.create",{article_id:"article-new",task_type:"text",model:"model-1",skill_ids:["skill-1"],customizations:"",request_id:"disabled-create"}],["generation.retry",{job_id:"job-1",request_id:"disabled-retry"}],["generation.input",{job_id:"job-1",answers:{answer:"Continue"}}]]) assert.equal((await command(operation,payload)).status,409);
  assert.equal((await db.prepare("SELECT COUNT(*) AS n FROM command_queue").first()).n,0);
  assert.equal((await command("generation.save",{job_id:"job-1",output_text:"Manual edit"})).status,202);
  assert.equal((await command("generation.cancel",{job_id:"job-1"})).status,202);
  environment.GENERATION_ENABLED="true";
  assert.equal((await command("generation.create",{article_id:"article-new",task_type:"text",model:"model-1",skill_ids:["skill-1"],customizations:"",request_id:"enabled-create"})).status,202);
});

test("valid authenticated notification staging and completion refresh heartbeat while malformed sync does not",async () => {
  const db=await populatedDatabase();
  const environment={DB:db,ASSETS:{fetch:async()=>new Response("Not found",{status:404})},AUTH_BASE_URL:"http://localhost",BRIDGE_SECRET:"test-bridge-secret-long-enough-for-authentication"};
  const put=async(payload,validSignature=true)=>{
    const body=JSON.stringify(payload),timestamp=String(Date.now()),nonce=crypto.randomUUID();
    const canonical=["v1",timestamp,nonce,"PUT","/api/bridge/sync",createHash("sha256").update(body).digest("hex")].join("\n");
    const signature=validSignature?createHmac("sha256",environment.BRIDGE_SECRET).update(canonical).digest("hex"):"0".repeat(64);
    globalThis.__CLOUDFLARE_TEST_ENV__=environment;
    return worker.fetch(new Request("http://localhost/api/bridge/sync",{method:"PUT",headers:{"content-type":"application/json","x-news-wire-timestamp":timestamp,"x-news-wire-nonce":nonce,"x-news-wire-signature":signature,"x-news-wire-bridge-version":"2.3.0","x-news-wire-runtime-version":"0.6.0"},body}),environment,context);
  };
  const envelope={schema_version:2,kind:"notification_articles",sync_id:syncId,mode:"full",notification_articles:[{event_id:1,article_key:"c".repeat(64),story_id:"story-1",canonical_url:"https://example.com/news",title:"News",publisher:"Publisher",category:"Open ecosystem",context:"Publisher reports news.",provenance:"publisher_excerpt",source_type:"article",published_at:null,detected_at:new Date().toISOString()}],deleted_ids:[]};
  await db.prepare("UPDATE bridge_status SET last_seen_at=1,last_error='stale'").run();
  assert.equal((await put({...envelope,notification_articles:[{...envelope.notification_articles[0],title:""}]})).status,400);
  assert.equal((await put(envelope,false)).status,401);
  assert.equal((await db.prepare("SELECT last_seen_at FROM bridge_status").first()).last_seen_at,1);
  assert.equal((await put(envelope)).status,200);
  assert.ok((await db.prepare("SELECT last_seen_at FROM bridge_status").first()).last_seen_at>Date.now()-5000);
  await db.prepare("UPDATE bridge_status SET last_seen_at=1,last_error='stale'").run();
  const completion={schema_version:2,kind:"complete",sync_id:syncId,projection:"notification_articles",digest:"c".repeat(64),total:1,mode:"full"};
  assert.equal((await put({...completion,total:2})).status,409);
  assert.equal((await db.prepare("SELECT last_seen_at FROM bridge_status").first()).last_seen_at,1);
  assert.equal((await put(completion)).status,200);
  const heartbeat=await db.prepare("SELECT last_seen_at,last_error FROM bridge_status").first();
  assert.ok(heartbeat.last_seen_at>Date.now()-5000);assert.equal(heartbeat.last_error,null);
  assert.equal((await db.prepare("SELECT notification_digest FROM projection_state").first()).notification_digest,completion.digest);
});

test("bookmarked story routes open each matching article workspace and retain legacy drafts",async () => {
  const db=await populatedDatabase();
  await saveResourceChunk(db,syncId,"full",[
    {resource_type:"story_detail",resource_id:"story-bookmarked",rank:0,payload:{id:"story-bookmarked",headline:"Saved story history",sources:[{url:"https://example.com/original",title:"Historic source"}],drafts:[{id:42,version:3,headline:"Earlier saved draft"}]}},
    {resource_type:"article_detail",resource_id:"article-first",rank:0,payload:{id:"article-first",story_id:"story-bookmarked",headline:"First fetched article",publisher:"First publisher",canonical_url:"https://example.com/first"}},
    {resource_type:"article_detail",resource_id:"article-second",rank:1,payload:{id:"article-second",story_id:"story-bookmarked",headline:"Second fetched article",publisher:"Second publisher",canonical_url:"http://example.com/second"}},
    {resource_type:"article_detail",resource_id:"article-other",rank:2,payload:{id:"article-other",story_id:"story-bookmarked-other",headline:"Unrelated article"}},
    {resource_type:"generation_job",resource_id:"job-other",rank:0,payload:{id:"job-other",story_id:"story-bookmarked"}},
    {resource_type:"generation_catalog",resource_id:"runtime",rank:0,payload:{ready:true,models:[{id:"model-1"}],skills:[{id:"skill-1",name:"Writing"}]}},
  ],[]);
  assert.deepEqual((await listStoryArticles(db,"story-bookmarked")).map((article) => article.id),["article-first","article-second"]);
  assert.deepEqual(await listStoryArticles(db,"story-bookmarked' OR 1=1 --"),[]);
  const environment={DB:db,ASSETS:{fetch:async () => new Response("Not found",{status:404})},AUTH_BASE_URL:"http://localhost",AUTH_SECRET:"test-auth-secret-long-enough-for-tests",MASTER_PASSWORD_VERSION:"1",GENERATION_ENABLED:"true"};
  const token=await createSession(environment,{actorId:"master",role:"master",email:null,credentialVersion:"1",lifetimeSeconds:60});
  const fetchApp=(path,authenticated=true) => {globalThis.__CLOUDFLARE_TEST_ENV__=environment;return worker.fetch(new Request(`http://localhost${path}`,{headers:authenticated?{cookie:`osainw_session=${token}`}:{},redirect:"manual"}),environment,context);};
  const response=await fetchApp("/dashboard/stories/story-bookmarked");assert.equal(response.status,200);
  const html=await response.text();
  assert.match(html,/href="\/dashboard\/articles\/article-first"/);assert.match(html,/href="\/dashboard\/articles\/article-second"/);
  assert.equal((html.match(/>Open article workspace<\/a>/gu)||[]).length,2);
  assert.match(html,/First fetched article/);assert.match(html,/Second fetched article/);assert.match(html,/http:\/\/example.com\/second/);
  assert.match(html,/Historic source/);assert.match(html,/Earlier saved draft/);assert.match(html,/href="\/dashboard\/drafts\/42"/);
  assert.doesNotMatch(html,/Unrelated article|Generation setup is pending|generation-panel/);
  for(const id of ["article-first","article-second"]) {
    const workspace=await fetchApp(`/dashboard/articles/${id}`);assert.equal(workspace.status,200);
    const workspaceHtml=await workspace.text();assert.match(workspaceHtml,/Generate content/);assert.match(workspaceHtml,/Generate graphics/);
  }
});

test("stage-one dashboard retains old bridge history without unsupported reads or inference",async () => {
  const db=await populatedDatabase();
  await saveProjectionState(db,{syncId,storyDigest:"a".repeat(64),storyTotal:4,resourceDigest:"b".repeat(64),resourceTotal:1,snapshot,bridgeVersion:"2.2.0"});
  await saveStoryChunk(db,syncId,"delta",[{id:"story-cached",headline:"Cached historical news",status:"ready",detected_at:"2026-10-13T11:00:00Z",is_review_current:false}],[]);
  await db.prepare("UPDATE story_projection SET first_fetched_at='' WHERE id='story-cached'").run();
  await saveResourceChunk(db,syncId,"full",[{resource_type:"story_detail",resource_id:"story-cached",rank:0,payload:{id:"story-cached",headline:"Cached historical news",sources:[{url:"https://example.com/history",title:"Original source"}],drafts:[]}}],[]);
  assert.equal((await listStoryPage(db,{window:"review_now",pageSize:1})).stories[0].id,"story-cached");
  const environment={DB:db,ASSETS:{fetch:async () => new Response("Not found",{status:404})},AUTH_BASE_URL:"http://localhost",AUTH_SECRET:"test-auth-secret-long-enough-for-tests",MASTER_PASSWORD_VERSION:"1"};
  const token=await createSession(environment,{actorId:"master",role:"master",email:null,credentialVersion:"1",lifetimeSeconds:60});
  const fetchApp=async (path,method="GET") => {globalThis.__CLOUDFLARE_TEST_ENV__=environment;return worker.fetch(new Request(`http://localhost${path}`,{method,headers:{cookie:`osainw_session=${token}`,origin:"http://localhost"},redirect:"manual"}),environment,context);};
  const inbox=await fetchApp("/dashboard/inbox");assert.equal(inbox.status,200);const inboxHtml=await inbox.text();assert.match(inboxHtml,/Cached historical news/);assert.match(inboxHtml,/href="\/dashboard\/stories\/story-cached"/);assert.doesNotMatch(inboxHtml,/No fetched articles yet/);
  const history=await fetchApp("/dashboard/stories/story-cached");assert.equal(history.status,200);const historyHtml=await history.text();assert.match(historyHtml,/Original source/);assert.match(historyHtml,/Generate content/);assert.match(historyHtml,/Generate graphics/);assert.doesNotMatch(historyHtml,/Priority floor|Confirm evidence|Review score/);
  const status=await fetchApp("/api/dashboard/articles/missing/generation");assert.equal(status.status,200);assert.equal((await status.json()).supportsGeneration,false);
  assert.equal((await fetchApp("/api/dashboard/articles/missing/generation","POST")).status,409);
  assert.equal((await db.prepare("SELECT COUNT(*) AS n FROM read_request").first()).n,0);
});
