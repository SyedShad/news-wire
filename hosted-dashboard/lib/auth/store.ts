import type { AuthRuntimeEnv, AuthSession, Role } from "./types.ts";
import { randomToken, sha256 } from "./crypto.ts";
import { masterPasswordVersion } from "./config.ts";

const schemaPromises = new WeakMap<object, Promise<void>>();

export async function ensureAuthSchema(db: D1Database): Promise<void> {
  const key = db as unknown as object;
  const existing = schemaPromises.get(key);
  if (existing) return existing;

  const setup = db
    .batch([
      db.prepare(`CREATE TABLE IF NOT EXISTS auth_sessions (
        session_hash TEXT PRIMARY KEY NOT NULL,
        actor_id TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('editor', 'master')),
        email TEXT,
        credential_version TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL
      )`),
      db.prepare(
        "CREATE INDEX IF NOT EXISTS auth_sessions_expires_at_idx ON auth_sessions (expires_at)",
      ),
      db.prepare(`CREATE TABLE IF NOT EXISTS oauth_transactions (
        state_hash TEXT PRIMARY KEY NOT NULL,
        verifier TEXT NOT NULL,
        nonce TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL
      )`),
      db.prepare(
        "CREATE INDEX IF NOT EXISTS oauth_transactions_expires_at_idx ON oauth_transactions (expires_at)",
      ),
      db.prepare(`CREATE TABLE IF NOT EXISTS master_login_state (
        fingerprint TEXT PRIMARY KEY NOT NULL,
        failures INTEGER NOT NULL,
        next_allowed_at INTEGER NOT NULL,
        locked_until INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
      )`),
      db.prepare(`CREATE TABLE IF NOT EXISTS audit_events (
        id TEXT PRIMARY KEY NOT NULL,
        created_at INTEGER NOT NULL,
        actor_id TEXT,
        role TEXT,
        action TEXT NOT NULL,
        outcome TEXT NOT NULL,
        ip_hash TEXT,
        detail TEXT
      )`),
      db.prepare(
        "CREATE INDEX IF NOT EXISTS audit_events_created_at_idx ON audit_events (created_at)",
      ),
    ])
    .then(() => undefined)
    .catch((error) => {
      schemaPromises.delete(key);
      throw error;
    });
  schemaPromises.set(key, setup);
  return setup;
}

export async function createSession(
  source: AuthRuntimeEnv,
  input: {
    actorId: string;
    role: Role;
    email: string | null;
    credentialVersion: string;
    lifetimeSeconds: number;
  },
): Promise<string> {
  await ensureAuthSchema(source.DB);
  const token = randomToken(32);
  const sessionHash = await sha256(token);
  const createdAt = Date.now();
  const expiresAt = createdAt + input.lifetimeSeconds * 1_000;
  await source.DB.prepare(
    `INSERT INTO auth_sessions
      (session_hash, actor_id, role, email, credential_version, created_at, expires_at)
     VALUES (?, ?, ?, ?, ?, ?, ?)`,
  )
    .bind(
      sessionHash,
      input.actorId,
      input.role,
      input.email,
      input.credentialVersion,
      createdAt,
      expiresAt,
    )
    .run();
  return token;
}

type StoredSession = {
  actor_id: string;
  role: Role;
  email: string | null;
  credential_version: string;
  created_at: number;
  expires_at: number;
};

export async function readSession(
  source: AuthRuntimeEnv,
  token: string | undefined,
): Promise<AuthSession | null> {
  if (!token) return null;
  await ensureAuthSchema(source.DB);
  const sessionHash = await sha256(token);
  const row = await source.DB.prepare(
    `SELECT actor_id, role, email, credential_version, created_at, expires_at
       FROM auth_sessions WHERE session_hash = ?`,
  )
    .bind(sessionHash)
    .first<StoredSession>();
  if (!row) return null;

  const expired = row.expires_at <= Date.now();
  const rotated =
    row.role === "master" &&
    row.credential_version !== masterPasswordVersion(source);
  if (expired || rotated) {
    await source.DB.prepare("DELETE FROM auth_sessions WHERE session_hash = ?")
      .bind(sessionHash)
      .run();
    return null;
  }

  return {
    actorId: row.actor_id,
    role: row.role,
    email: row.email,
    createdAt: row.created_at,
    expiresAt: row.expires_at,
  };
}

export async function deleteSession(
  source: AuthRuntimeEnv,
  token: string | undefined,
): Promise<void> {
  if (!token) return;
  await ensureAuthSchema(source.DB);
  await source.DB.prepare("DELETE FROM auth_sessions WHERE session_hash = ?")
    .bind(await sha256(token))
    .run();
}

export async function storeOauthTransaction(
  source: AuthRuntimeEnv,
  input: { state: string; verifier: string; nonce: string },
): Promise<void> {
  await ensureAuthSchema(source.DB);
  const now = Date.now();
  await source.DB.prepare(
    `INSERT INTO oauth_transactions
      (state_hash, verifier, nonce, created_at, expires_at)
     VALUES (?, ?, ?, ?, ?)`,
  )
    .bind(await sha256(input.state), input.verifier, input.nonce, now, now + 600_000)
    .run();
}

type StoredOauthTransaction = {
  verifier: string;
  nonce: string;
  expires_at: number;
};

export async function consumeOauthTransaction(
  source: AuthRuntimeEnv,
  state: string,
): Promise<{ verifier: string; nonce: string } | null> {
  await ensureAuthSchema(source.DB);
  const stateHash = await sha256(state);
  const row = await source.DB.prepare(
    "SELECT verifier, nonce, expires_at FROM oauth_transactions WHERE state_hash = ?",
  )
    .bind(stateHash)
    .first<StoredOauthTransaction>();
  await source.DB.prepare("DELETE FROM oauth_transactions WHERE state_hash = ?")
    .bind(stateHash)
    .run();
  if (!row || row.expires_at <= Date.now()) return null;
  return { verifier: row.verifier, nonce: row.nonce };
}

export type MasterAttemptState = {
  failures: number;
  nextAllowedAt: number;
  lockedUntil: number;
};

type StoredMasterAttemptState = {
  failures: number;
  next_allowed_at: number;
  locked_until: number;
};

export async function readMasterAttemptState(
  source: AuthRuntimeEnv,
  fingerprint: string,
): Promise<MasterAttemptState> {
  await ensureAuthSchema(source.DB);
  const row = await source.DB.prepare(
    `SELECT failures, next_allowed_at, locked_until
       FROM master_login_state WHERE fingerprint = ?`,
  )
    .bind(fingerprint)
    .first<StoredMasterAttemptState>();
  return row
    ? {
        failures: row.failures,
        nextAllowedAt: row.next_allowed_at,
        lockedUntil: row.locked_until,
      }
    : { failures: 0, nextAllowedAt: 0, lockedUntil: 0 };
}

export async function recordMasterFailure(
  source: AuthRuntimeEnv,
  fingerprint: string,
  previousFailures: number,
): Promise<MasterAttemptState> {
  await ensureAuthSchema(source.DB);
  const now = Date.now();
  const failures = previousFailures + 1;
  const lockedUntil = failures >= 5 ? now + 15 * 60_000 : 0;
  const nextAllowedAt = now + 2_000;
  await source.DB.prepare(
    `INSERT INTO master_login_state
      (fingerprint, failures, next_allowed_at, locked_until, updated_at)
     VALUES (?, ?, ?, ?, ?)
     ON CONFLICT(fingerprint) DO UPDATE SET
       failures = excluded.failures,
       next_allowed_at = excluded.next_allowed_at,
       locked_until = excluded.locked_until,
       updated_at = excluded.updated_at`,
  )
    .bind(fingerprint, failures, nextAllowedAt, lockedUntil, now)
    .run();
  return { failures, nextAllowedAt, lockedUntil };
}

export async function clearMasterFailures(
  source: AuthRuntimeEnv,
  fingerprint: string,
): Promise<void> {
  await ensureAuthSchema(source.DB);
  await source.DB.prepare("DELETE FROM master_login_state WHERE fingerprint = ?")
    .bind(fingerprint)
    .run();
}

export async function audit(
  source: AuthRuntimeEnv,
  event: {
    actorId?: string | null;
    role?: Role | null;
    action: string;
    outcome: string;
    ipHash?: string | null;
    detail?: Record<string, string | number | boolean | null>;
  },
): Promise<void> {
  await ensureAuthSchema(source.DB);
  await source.DB.prepare(
    `INSERT INTO audit_events
      (id, created_at, actor_id, role, action, outcome, ip_hash, detail)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
  )
    .bind(
      crypto.randomUUID(),
      Date.now(),
      event.actorId ?? null,
      event.role ?? null,
      event.action,
      event.outcome,
      event.ipHash ?? null,
      event.detail ? JSON.stringify(event.detail) : null,
    )
    .run();
}
