import type { JsonValue } from "@/lib/dashboard/types.ts";

export type RecordValue = Record<string, JsonValue>;

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
  return Number.isNaN(parsed.valueOf()) ? String(value) : parsed.toLocaleString();
}
