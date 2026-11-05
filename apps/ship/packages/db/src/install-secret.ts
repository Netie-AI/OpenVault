// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
/**
 * The per-install secret that seals every stored secret (S3/SFTP backup keys, git clone
 * tokens, SSH credentials, env vars) and signs session and MCP tokens.
 *
 * Resolution order:
 *   1. BETTER_AUTH_SECRET from the environment, when the operator set a real value.
 *   2. The file `auth-secret` in the state directory (mode 0600), when it exists.
 *   3. A new random 32-byte secret, written to that file with mode 0600 on first use.
 *
 * The old placeholder `change-me-in-production` is not a secret: it ships in .env.example.
 * An empty value or the placeholder counts as unset, so a copied .env.example gets a
 * generated key instead of a public one. Data an earlier build sealed under the
 * placeholder still opens through `createEncryption(secret, { fallbackSecrets })`.
 *
 * State directory: OPENSHIP_AUTH_SECRET_FILE (a full file path) wins, else
 * `$OPENSHIP_DATA_DIR/auth-secret`, else the parent of an explicit PGLITE_DATA_DIR, else
 * `~/.openship/auth-secret`. Keep it on a volume that
 * survives the process: losing the file makes every value sealed under it unreadable.
 *
 * Under a test runner (VITEST, NODE_ENV=test) nothing is written and the legacy placeholder
 * is returned, so unit suites keep their fixed key and never touch the home directory.
 *
 * Passive like encryption.ts: importing it reads no environment and opens no database.
 */

import { randomBytes } from "node:crypto";
import { chmodSync, linkSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { DEFAULT_ENCRYPTION_SECRET } from "./encryption";

export const INSTALL_SECRET_FILENAME = "auth-secret";
const MIN_SECRET_LENGTH = 32;

export type InstallSecretSource = "env" | "file" | "generated" | "test-default";

export interface InstallSecret {
  readonly secret: string;
  readonly source: InstallSecretSource;
  /** The file holding the secret (sources "file" and "generated"). */
  readonly path?: string;
}

export interface InstallSecretOptions {
  /** Raw BETTER_AUTH_SECRET value. */
  readonly envSecret?: string | undefined;
  /** Full path of the secret file. */
  readonly file?: string;
  /** Skip the file and return the legacy placeholder. Defaults to "running under a test runner". */
  readonly testMode?: boolean;
}

/** True for an unset, empty or placeholder value: nothing an operator chose. */
export function isPlaceholderSecret(value: string | undefined | null): boolean {
  const trimmed = value?.trim();
  return !trimmed || trimmed === DEFAULT_ENCRYPTION_SECRET;
}

export function installSecretPath(env: NodeJS.ProcessEnv = process.env): string {
  const explicit = env.OPENSHIP_AUTH_SECRET_FILE?.trim();
  if (explicit) return resolve(explicit);
  const home = env.HOME ?? env.USERPROFILE ?? homedir();
  const dataDir = env.OPENSHIP_DATA_DIR?.trim();
  if (dataDir) return join(resolve(dataDir), INSTALL_SECRET_FILENAME);
  // Next to an explicit PGlite directory, so the volume an operator already keeps holds both.
  const pglite = env.PGLITE_DATA_DIR?.trim();
  if (pglite && pglite !== "memory://") {
    const expanded = pglite === "~" || pglite.startsWith("~/") ? resolve(home, pglite.slice(1).replace(/^\/+/, "")) : pglite;
    return join(dirname(resolve(expanded)), INSTALL_SECRET_FILENAME);
  }
  return join(home, ".openship", INSTALL_SECRET_FILENAME);
}

function isTestRun(): boolean {
  return Boolean(process.env.VITEST) || process.env.NODE_ENV === "test";
}

function errnoCode(error: unknown): string | undefined {
  return typeof error === "object" && error !== null ? (error as { code?: string }).code : undefined;
}

function readSecretFile(path: string): string | undefined {
  let text: string;
  try {
    text = readFileSync(path, "utf8").trim();
  } catch (error) {
    if (errnoCode(error) === "ENOENT") return undefined;
    throw error;
  }
  // An empty file is a crash between create and write: nothing was ever sealed under it.
  if (text === "") {
    rmSync(path, { force: true });
    return undefined;
  }
  if (text.length < MIN_SECRET_LENGTH) {
    throw new Error(
      `${path} holds fewer than ${MIN_SECRET_LENGTH} characters. Put a random secret there ` +
        `(for example: node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"), ` +
        `or delete the file to have one generated.`,
    );
  }
  if (process.platform !== "win32") {
    try {
      if ((statSync(path).mode & 0o077) !== 0) chmodSync(path, 0o600);
    } catch {
      // Best effort: the secret is readable either way, and a read-only mount cannot be tightened.
    }
  }
  return text;
}

/** Create `path` holding `secret` with mode 0600. Returns false when another process created it first. */
function createSecretFile(path: string, secret: string): boolean {
  mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
  const temp = `${path}.${process.pid}.${randomBytes(4).toString("hex")}.tmp`;
  writeFileSync(temp, `${secret}\n`, { flag: "wx", mode: 0o600 });
  try {
    // A hard link is atomic and fails if the target exists, so the file is never half-written.
    linkSync(temp, path);
    return true;
  } catch (error) {
    if (errnoCode(error) === "EEXIST") return false;
    try {
      writeFileSync(path, `${secret}\n`, { flag: "wx", mode: 0o600 });
      return true;
    } catch (fallback) {
      if (errnoCode(fallback) === "EEXIST") return false;
      throw fallback;
    }
  } finally {
    rmSync(temp, { force: true });
  }
}

export function resolveInstallSecret(options: InstallSecretOptions = {}): InstallSecret {
  const envSecret = options.envSecret;
  if (!isPlaceholderSecret(envSecret)) return { secret: envSecret!, source: "env" };

  if (options.testMode ?? isTestRun()) return { secret: DEFAULT_ENCRYPTION_SECRET, source: "test-default" };

  const path = options.file ?? installSecretPath();
  for (let attempt = 0; attempt < 3; attempt++) {
    const existing = readSecretFile(path);
    if (existing) return { secret: existing, source: "file", path };
    const generated = randomBytes(MIN_SECRET_LENGTH).toString("hex");
    if (createSecretFile(path, generated)) return { secret: generated, source: "generated", path };
  }
  throw new Error(`Could not create or read the install secret at ${path}. Set BETTER_AUTH_SECRET instead.`);
}

const MEMO = Symbol.for("freebuild.installSecret");

/**
 * Process-wide resolution. env.ts and the database client both call this with the same raw
 * BETTER_AUTH_SECRET, so the platform and the stores they seal for always agree on one key,
 * even when the module is loaded twice.
 */
export function resolveInstallSecretOnce(envSecret: string | undefined): InstallSecret {
  const store = ((globalThis as unknown as Record<symbol, unknown>)[MEMO] ??= new Map<string, InstallSecret>()) as Map<string, InstallSecret>;
  const memoKey = envSecret ?? "";
  let hit = store.get(memoKey);
  if (!hit) {
    hit = resolveInstallSecret({ envSecret });
    store.set(memoKey, hit);
  }
  return hit;
}
