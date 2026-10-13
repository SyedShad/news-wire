import type { JsonValue } from "./types.ts";
export const MAX_ARTIFACT_BYTES = 25 * 1024 * 1024;
export const MAX_ARTIFACT_CHUNK_BYTES = 500 * 1024;
const PREVIEW_TYPES = new Set(["image/png", "image/jpeg", "image/webp", "image/gif", "application/pdf"]);
export function artifactDisposition(mime: string, download: boolean): "inline" | "attachment" {return !download && PREVIEW_TYPES.has(mime) ? "inline" : "attachment";}
export function assembleArtifact(metadata: Record<string, JsonValue>, chunks: Array<Record<string, JsonValue>>): Uint8Array<ArrayBuffer> {
  const size=Number(metadata.size_bytes);
  const count=Number(metadata.chunk_count);
  if (!Number.isSafeInteger(size) || size < 0 || size > MAX_ARTIFACT_BYTES || !Number.isSafeInteger(count) || count < 1 || count > 52 || chunks.length !== count) throw new Error("artifact_incomplete");
  const data=new Uint8Array(size);let offset=0;
  for (let index=0;index<count;index+=1) {
    const chunk=chunks[index];
    if (chunk.index !== index || typeof chunk.data_base64 !== "string" || chunk.data_base64.length > Math.ceil(MAX_ARTIFACT_CHUNK_BYTES/3)*4 || (chunk.data_base64.length % 4 !== 0 || !/^[A-Za-z0-9+/]*={0,2}$/u.test(chunk.data_base64))) throw new Error("artifact_chunk_invalid");
    const decoded=atob(chunk.data_base64);
    if (decoded.length > MAX_ARTIFACT_CHUNK_BYTES || offset+decoded.length > size) throw new Error("artifact_size_invalid");
    for(let position=0;position<decoded.length;position+=1) data[offset+position]=decoded.charCodeAt(position);
    offset+=decoded.length;
  }
  if (offset !== size) throw new Error("artifact_size_invalid");
  return data;
}

export async function verifyArtifactDigest(metadata: Record<string, JsonValue>, data: Uint8Array<ArrayBuffer>): Promise<void> {
  if (typeof metadata.sha256 !== "string" || !/^[a-f0-9]{64}$/iu.test(metadata.sha256)) throw new Error("artifact_digest_missing");
  const digest=await crypto.subtle.digest("SHA-256",data);
  const actual=Array.from(new Uint8Array(digest),(value) => value.toString(16).padStart(2,"0")).join("");
  if(actual !== metadata.sha256.toLowerCase()) throw new Error("artifact_digest_mismatch");
}
