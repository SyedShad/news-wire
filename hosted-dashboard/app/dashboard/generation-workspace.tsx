"use client";

/* Authenticated artifact previews deliberately use the original route without an image optimizer. */
/* eslint-disable @next/next/no-img-element */

import { useEffect, useState } from "react";
import { CommandMessage, useOwnerCommand } from "./command-client.tsx";
import { dateTime, records, text, type RecordValue } from "./presentation.ts";

const RUNNING = new Set(["queued", "pending", "starting", "running", "generating", "waiting", "waiting_for_input", "needs_input", "awaiting_input", "cancel_requested"]);
const INPUT = new Set(["waiting_for_input", "needs_input", "awaiting_input"]);
function questionText(job: RecordValue): string {
  if (typeof job.question === "string") return job.question;
  if (job.question && typeof job.question === "object" && !Array.isArray(job.question)) return text(job.question, "prompt", text(job.question, "question", text(job.question, "message", "Additional input is needed.")));
  return "Additional input is needed.";
}
export default function GenerationWorkspace({ articleId, initialCatalog, initialJobs, initialConnected, initialEnabled, initialSupportsGeneration }: {articleId: string; initialCatalog: RecordValue | null; initialJobs: RecordValue[]; initialConnected: boolean;initialEnabled: boolean;initialSupportsGeneration: boolean}) {
  const [catalog, setCatalog] = useState(initialCatalog);
  const [jobs, setJobs] = useState(initialJobs);
  const [connected, setConnected] = useState(initialConnected);
  const [enabled, setEnabled] = useState(initialEnabled);
  const [supportsGeneration, setSupportsGeneration] = useState(initialSupportsGeneration);
  const [pollError, setPollError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  useEffect(() => {
    const abort = new AbortController();
    const refresh = async () => {
      if (document.hidden) return;
      try {
        const response = await fetch(`/api/dashboard/articles/${encodeURIComponent(articleId)}/generation`, {cache: "no-store", signal: abort.signal});
        if (!response.ok) throw new Error("Generation status could not be refreshed.");
        const result = await response.json() as {catalog: RecordValue | null; jobs: RecordValue[]; connected: boolean;enabled:boolean;supportsGeneration:boolean};
        setCatalog(result.catalog); setJobs(result.jobs); setConnected(result.connected); setPollError("");
        setEnabled(result.enabled === true);setSupportsGeneration(result.supportsGeneration === true);
      } catch (error) { if (!abort.signal.aborted) setPollError(error instanceof Error ? error.message : "Refresh failed."); }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 3_000);
    return () => {abort.abort();window.clearInterval(timer);};
  }, [articleId]);
  return <div className="generation-workspace">
    <div className="header-actions"><button className="button button-secondary" disabled={!connected || !supportsGeneration || refreshing} onClick={async () => {setRefreshing(true);try {const result=await fetch(`/api/dashboard/articles/${encodeURIComponent(articleId)}/generation`,{method:"POST"});if (!result.ok) throw new Error("The catalog refresh could not be requested.");setPollError("");}catch(error){setPollError(error instanceof Error ? error.message : "Refresh failed.");}finally{setRefreshing(false);}}}>{refreshing ? "Requesting refresh…" : "Refresh models and skills"}</button></div>
    {pollError ? <p className="owner-warning" role="status">{pollError}</p> : null}
    {!connected ? <p className="owner-warning">Your laptop is offline. Saved results remain available; generation actions resume when it reconnects.</p> : null}
    {!supportsGeneration ? <p className="owner-warning">Generation setup needs the upgraded laptop runtime and bridge. Cached news and saved content remain available.</p> : !enabled ? <p className="owner-message">Generation setup is pending local authentication and real-run validation. Saved work remains available.</p> : null}
    <div className="generation-panels">{(["text", "graphics"] as const).map((task) => <GenerationPanel key={task} task={task} articleId={articleId} catalog={catalog} connected={connected} enabled={enabled && supportsGeneration} jobs={jobs.filter((job) => job.task_type === task)} />)}</div>
  </div>;
}
function GenerationPanel({ task, articleId, catalog, connected, enabled, jobs }: {task: "text" | "graphics";articleId: string;catalog: RecordValue | null;connected: boolean;enabled:boolean;jobs: RecordValue[]}) {
  const models = records(catalog || undefined, "models");
  const skills = records(catalog || undefined, "skills");
  const preferences = catalog?.preferences && typeof catalog.preferences === "object" && !Array.isArray(catalog.preferences) ? catalog.preferences : undefined;
  const saved = preferences?.[task] && typeof preferences[task] === "object" && !Array.isArray(preferences[task]) ? preferences[task] as RecordValue : undefined;
  const defaultModel = text(saved, "model", text(catalog || undefined, "default_model", ""));
  const [selectedModel, setSelectedModel] = useState<string | null>(null);
  const [selectedSkills, setSelectedSkills] = useState<string[] | null>(null);
  const [customizations, setCustomizations] = useState("");
  const {queue, message, busy} = useOwnerCommand();
  const model = selectedModel ?? defaultModel;
  const skillIds = selectedSkills ?? (Array.isArray(saved?.skill_ids) ? saved.skill_ids.filter((value): value is string => typeof value === "string") : []);
  const catalogError = text(catalog || undefined, "error", "");
  const authReady = catalog?.ready === true;
  const authMessage = text(catalog || undefined,"auth_message","");
  const missingSkills = skillIds.some((id) => !skills.some((skill) => skill.id === id));
  const unavailable = !enabled || !authReady || !connected || busy || !skillIds.length || !models.some((item) => item.id === model) || Boolean(catalogError) || missingSkills;
  const addSkill = (id: string) => {if (id && !skillIds.includes(id)) setSelectedSkills([...skillIds, id]);};
  const moveSkill = (index: number, direction: number) => {const next=[...skillIds];const target=index+direction;if (target < 0 || target >= next.length) return;[next[index],next[target]]=[next[target],next[index]];setSelectedSkills(next);};
  return <section className="panel generation-panel"><div className="panel-heading"><div><p className="section-index">{task === "text" ? "01" : "02"}</p><h2>{task === "text" ? "Generate content" : "Generate graphics"}</h2></div></div>
    <div className="generation-form"><label>ChatGPT model<select value={model} onChange={(event) => setSelectedModel(event.target.value)} disabled={busy || !models.length}><option value="">Select a model</option>{models.map((item) => <option key={String(item.id)} value={String(item.id)}>{text(item, "display_name", String(item.id))}</option>)}</select></label>
      <label>Add a skill<select value="" onChange={(event) => addSkill(event.target.value)} disabled={busy || !skills.length || skillIds.length >= 20}><option value="">Select a skill</option>{skills.filter((skill) => !skillIds.includes(String(skill.id))).map((skill) => <option key={String(skill.id)} value={String(skill.id)}>{text(skill, "name", String(skill.id))}</option>)}</select></label>
      <p className="muted">Skills run in the order shown. You can choose multiple skills for this task.</p>
      <ol className="selected-skills">{skillIds.map((id,index) => {const skill=skills.find((item) => item.id === id);return <li key={id}><div><strong>{skill ? text(skill,"name",id) : "Unavailable skill"}</strong>{skill ? <small>{text(skill,"description","")}</small> : null}</div><div className="button-row"><button className="button button-small" disabled={busy || index === 0} onClick={() => moveSkill(index,-1)} aria-label={`Move ${skill ? text(skill,"name",id) : "skill"} earlier`}>↑</button><button className="button button-small" disabled={busy || index === skillIds.length-1} onClick={() => moveSkill(index,1)} aria-label={`Move ${skill ? text(skill,"name",id) : "skill"} later`}>↓</button><button className="button button-small" disabled={busy} onClick={() => setSelectedSkills(skillIds.filter((value) => value !== id))} aria-label={`Remove ${skill ? text(skill,"name",id) : "skill"}`}>Remove</button></div></li>;})}</ol>
      <label>Customizations<textarea value={customizations} onChange={(event) => setCustomizations(event.target.value)} maxLength={10000} rows={4} placeholder={task === "text" ? "Tone, audience, length, angle or other instructions" : "Format, dimensions, branding, visual style or other instructions"} /></label>
      {!catalog ? <p className="owner-message" role="status">Retrieving your installed skills and runtime models…</p> : null}
      {catalogError ? <p className="owner-warning">{catalogError}</p> : null}
      {catalog && !authReady ? <div className="owner-warning"><p>{authMessage || "Sign in to ChatGPT on your Mac to generate content and graphics."}</p><p>On your Mac, run <code>open-source-ai-news-wire generation login</code>, then refresh models and skills.</p></div> : null}
      {missingSkills ? <p className="owner-warning">An earlier selected skill is unavailable. Remove it or choose another installed skill.</p> : null}
      <button className="button button-primary" disabled={unavailable} onClick={() => queue("generation.create", {article_id: articleId, task_type: task, model, skill_ids: skillIds, customizations, request_id: crypto.randomUUID()})}>{busy ? "Requesting…" : task === "text" ? "Generate content" : "Generate graphics"}</button>
      <CommandMessage message={message} />
    </div>
    <div className="generation-history"><h3>{task === "text" ? "Content history" : "Graphics history"}</h3>{jobs.map((job, index) => <GenerationJob key={String(job.id)} job={job} connected={connected} enabled={enabled} version={jobs.length-index} skills={skills} />)}{!jobs.length ? <p className="muted">No {task === "text" ? "content" : "graphics"} generation yet.</p> : null}</div>
  </section>;
}
function GenerationJob({ job, connected, enabled, version, skills }: {job: RecordValue;connected: boolean;enabled:boolean;version: number;skills: RecordValue[]}) {
  const {queue, message, busy} = useOwnerCommand();
  const [edited, setEdited] = useState<string | null>(null);
  const [inputError, setInputError] = useState("");
  const [copied, setCopied] = useState(false);
  const status = text(job,"status","queued");
  const output = typeof job.output_text === "string" ? job.output_text : "";
  const active = RUNNING.has(status);
  const needsInput = INPUT.has(status);
  const artifacts = records(job,"artifacts");
  const downloadText = () => {
    const url = URL.createObjectURL(new Blob([edited ?? output], {type: "text/markdown;charset=utf-8"}));
    const link = document.createElement("a");
    link.href = url;
    link.download = `news-wire-content-${String(job.id).replace(/[^a-z0-9_-]/giu, "").slice(0, 80)}-v${String(job.version || version).replace(/[^a-z0-9_-]/giu, "")}.md`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return <article className="generation-job"><div className="generation-job-heading"><strong>{status.replaceAll("_"," ")}</strong><small>{dateTime(job.updated_at || job.created_at)} · {text(job,"model","")}{` · v${String(job.version || version)}`}</small></div>
    <details className="generation-job-inputs"><summary>Requested instructions</summary><p>{text(job,"customizations","No extra customizations.")}</p><p>Skills: {Array.isArray(job.skill_names) ? job.skill_names.join(" → ") : Array.isArray(job.skill_ids) ? job.skill_ids.map((id) => text(skills.find((skill) => skill.id === id),"name","Unavailable skill")).join(" → ") || "None" : "None"}</p></details>
    {job.error ? <p className="owner-warning">{typeof job.error === "string" ? job.error : JSON.stringify(job.error)}</p> : null}
    {needsInput ? <form onSubmit={(event) => {
      event.preventDefault(); const data=new FormData(event.currentTarget);
      const question=job.question && typeof job.question === "object" && !Array.isArray(job.question) ? job.question : undefined;
      const questions=records(question,"questions");
      try {
        const answers=questions.length ? Object.fromEntries(questions.map((item,index) => [text(item,"id",String(index)),String(data.get(`answer_${index}`) || "")])) : JSON.parse(String(data.get("answers_json") || "{}"));
        if (!answers || typeof answers !== "object" || Array.isArray(answers)) throw new Error("Provide a JSON object with the requested answers.");
        setInputError(""); void queue("generation.input",{job_id:String(job.id),answers});
      } catch(error) {setInputError(error instanceof Error ? error.message : "Check the requested input.");}
    }}><JobQuestions job={job} /><button className="button button-secondary" disabled={!enabled || !connected || busy}>Continue generation</button>{inputError ? <p className="owner-warning">{inputError}</p> : null}</form> : null}
    {output ? <div className="generation-output"><label>Generated content<textarea className="generation-output-editor" value={edited ?? output} onChange={(event) => {setEdited(event.target.value);setCopied(false);}} readOnly={job.task_type !== "text"} rows={10} maxLength={200000} /></label><div className="button-row"><button className="button button-small" onClick={async () => {try {await navigator.clipboard.writeText(edited ?? output);setCopied(true);}catch {setCopied(false);}}}>{copied ? "Copied" : "Copy"}</button><button className="button button-small" onClick={downloadText}>Download text</button>{job.task_type === "text" ? <button className="button button-small" disabled={!connected || busy || active || edited === null} onClick={async () => {const result=await queue("generation.save",{job_id:String(job.id),output_text:edited ?? output});if (result?.status === "completed") setEdited(null);}}>Save as new version</button> : null}</div></div> : null}
    {artifacts.length ? <div className="generation-artifacts">{artifacts.map((artifact,index) => {if(artifact.error) return <p className="owner-warning" key={String(artifact.id || index)}>{text(artifact,"name","Artifact")}: {text(artifact,"error","Download unavailable.")}</p>;const artifactId=text(artifact,"id","");const href=`/api/dashboard/generation/artifacts/${encodeURIComponent(artifactId)}`;const mime=text(artifact,"mime_type","");return <div className="generation-artifact" key={artifactId || index}>{["image/png","image/jpeg","image/webp","image/gif"].includes(mime) ? <a href={href} target="_blank" rel="noreferrer"><img src={href} alt={text(artifact,"name","Generated graphic")} loading="lazy" /></a> : <p>{text(artifact,"name","Generated artifact")}</p>}<a className="button button-small" href={`${href}?download=1`}>Download {text(artifact,"name","artifact")}</a></div>;})}</div> : null}
    <div className="button-row">{active ? <button className="button button-small button-danger" disabled={!connected || busy || status === "cancel_requested"} onClick={() => queue("generation.cancel",{job_id:String(job.id)})}>Cancel generation</button> : null}{["failed","cancelled","canceled","interrupted","completed"].includes(status) ? <button className="button button-small" disabled={!enabled || !connected || busy} onClick={() => queue("generation.retry",{job_id:String(job.id),request_id:crypto.randomUUID()})}>{status === "completed" ? "Generate another version" : "Retry"}</button> : null}</div>
    <CommandMessage message={message} />
  </article>;
}

function JobQuestions({job}: {job: RecordValue}) {
  const question=job.question && typeof job.question === "object" && !Array.isArray(job.question) ? job.question : undefined;
  const questions=records(question,"questions");
  return <>{questions.length ? questions.map((item,index) => {
    const options=records(item,"options");
    return <label key={text(item,"id",String(index))}>{text(item,"question",text(item,"prompt","Your response"))}{options.length ? <select name={`answer_${index}`} required defaultValue=""><option value="">Choose a response</option>{options.map((option) => <option key={text(option,"label","")} value={text(option,"label","")}>{text(option,"label","")}{text(option,"description","") ? ` — ${text(option,"description","")}` : ""}</option>)}</select> : <textarea name={`answer_${index}`} required maxLength={10000} rows={3} />}</label>;
  }) : <><p className="generation-question">{questionText(job)}</p>{question?.schema ? <pre className="generation-input-schema">{JSON.stringify(question.schema,null,2)}</pre> : null}<label>Requested answers as JSON<textarea name="answers_json" rows={4} maxLength={20000} required placeholder='{"answer": "Your response"}' /></label></>}</>;
}
