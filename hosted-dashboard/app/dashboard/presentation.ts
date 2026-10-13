import type { JsonValue } from "@/lib/dashboard/types.ts";

export type RecordValue = Record<string, JsonValue>;

const OPERATIONAL_NOTICE_KINDS = new Set(["health", "correction", "catch_up", "recovery", "generation"]);
export function operationalNotices(notices: RecordValue[]): RecordValue[] {
  return notices.filter((notice) => typeof notice.kind === "string" && OPERATIONAL_NOTICE_KINDS.has(notice.kind));
}

export function operationalUnreadCount(overview: RecordValue | undefined, notices: RecordValue[]): number {
  const counts = overview?.counts;
  if (counts && typeof counts === "object" && !Array.isArray(counts) && typeof counts.operational_unread === "number") {
    return Math.max(0, counts.operational_unread);
  }
  return operationalNotices(notices).filter((notice) => !notice.read_at).length;
}

export function text(record: RecordValue | undefined, key: string, fallback = "—"): string {
  const result = record?.[key];
  return typeof result === "string" && result.trim() ? result : fallback;
}

export function number(record: RecordValue | undefined, key: string): number {
  const result = record?.[key];
  return typeof result === "number" && Number.isFinite(result) ? result : 0;
}

export function records(record: RecordValue | undefined, key: string): RecordValue[] {
  const result = record?.[key];
  return Array.isArray(result)
    ? result.filter((item): item is RecordValue => Boolean(item) && typeof item === "object" && !Array.isArray(item))
    : [];
}

export function dateTime(value: JsonValue | undefined): string {
  if (typeof value !== "string" && typeof value !== "number") return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf())
    ? String(value)
    : parsed.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
}

export function fullDateTime(value: JsonValue | undefined): string {
  if (typeof value !== "string" && typeof value !== "number") return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? String(value) : parsed.toLocaleString();
}

export function safeExternalUrl(value: string): string | null {
  try { const url = new URL(value); return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password ? url.href : null; } catch { return null; }
}
