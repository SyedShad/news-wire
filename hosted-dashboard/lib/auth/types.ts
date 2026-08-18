export const APPROVED_GOOGLE_DOMAINS = [
  "sentient.foundation",
  "sentient.xyz",
] as const;

export type Role = "editor" | "master";

export type AuthSession = {
  actorId: string;
  role: Role;
  email: string | null;
  createdAt: number;
  expiresAt: number;
};

export type GoogleIdentity = {
  subject: string;
  email: string;
  domain: (typeof APPROVED_GOOGLE_DOMAINS)[number];
};

export type AuthRuntimeEnv = {
  DB: D1Database;
  CONTENT?: R2Bucket;
  AUTH_SECRET?: string;
  AUTH_BASE_URL?: string;
  GOOGLE_CLIENT_ID?: string;
  GOOGLE_CLIENT_SECRET?: string;
  MASTER_PASSWORD_VERIFIER?: string;
  MASTER_PASSWORD_VERSION?: string;
  BRIDGE_SECRET?: string;
};
