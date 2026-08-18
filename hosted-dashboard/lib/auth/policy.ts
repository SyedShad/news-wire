import { APPROVED_GOOGLE_DOMAINS } from "./types.ts";

export type ApprovedGoogleDomain = (typeof APPROVED_GOOGLE_DOMAINS)[number];

export function approvedEmailDomain(email: string): ApprovedGoogleDomain | null {
  const separator = email.lastIndexOf("@");
  if (separator <= 0 || separator === email.length - 1) return null;
  const domain = email.slice(separator + 1).toLowerCase();
  return APPROVED_GOOGLE_DOMAINS.find((allowed) => allowed === domain) ?? null;
}
