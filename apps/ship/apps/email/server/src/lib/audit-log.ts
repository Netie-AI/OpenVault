/**
 * Append-only JSONL audit log for authentication events.
 *
 * One line per event at `${dirname(SQLITE_PATH)}/auth.log`. Operators can grep
 * it, ship it to a log collector, or rotate it with logrotate - we make no
 * assumptions beyond "writable file."
 *
 * Events logged:
 *   sign-in:  every attempt (ok or not) with ip + email + reason on failure
 *   sign-out: every call, with the leaving sessionId
 *   switch:   every successful switch (denials are also logged as ok:false)
 *   rate-limit: when a request was blocked before reaching auth logic
 *
 * Failure modes:
 *   The file system can throw (disk full, perms). We catch and warn - losing
 *   an audit line is never a reason to break a login.
 */

import { appendFileSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { env } from '../env';

const AUTH_LOG_PATH = resolve(dirname(env.SQLITE_PATH), 'auth.log');
let dirEnsured = false;

function ensureLogDir(): void {
  if (dirEnsured) return;
  try {
    mkdirSync(dirname(AUTH_LOG_PATH), { recursive: true });
    dirEnsured = true;
  } catch {
    // Best-effort. If we can't create the dir, the appendFileSync below will
    // throw too and we'll fall into the catch in audit().
  }
}

export type AuthEventName = 'sign-in' | 'sign-out' | 'switch' | 'rate-limit';

export interface AuthEvent {
  event: AuthEventName;
  /** True if the operation succeeded. */
  ok: boolean;
  /** Best-effort client IP (XFF-aware, falls back to "unknown"). */
  ip: string;
  /** Email involved, when known. */
  email?: string;
  /** Session id involved, when known. */
  sessionId?: string;
  /** Free-form reason on failures (e.g. "invalid-password", "rate-limit-ip"). */
  reason?: string;
}

export function audit(evt: AuthEvent): void {
  ensureLogDir();
  const line = JSON.stringify({ ts: new Date().toISOString(), ...evt }) + '\n';
  try {
    appendFileSync(AUTH_LOG_PATH, line, { mode: 0o600 });
  } catch (err) {
    console.warn('[audit] failed to write auth event', err);
  }
}
