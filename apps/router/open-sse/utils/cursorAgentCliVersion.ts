/**
 * Cursor Agent CLI build id for the AgentService/Run `x-cursor-client-version`
 * header (`cli-${id}`, a dated build like `2026.07.08-0c04a8a`, not the IDE
 * `3.x` semver).
 *
 * Resolution: CURSOR_AGENT_CLI_VERSION env, then a local install, then the pin.
 * Nothing is fetched from cursor.com. The cursor and cursor-api providers are
 * hard-disabled in this edition (open-sse/netie/policy.ts), so this module only
 * stays for the dormant executor.
 */

import { existsSync, lstatSync, readdirSync, realpathSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { CURSOR_AGENT_CLI_VERSION } from "./cursorAgentCliVersionPin.ts";

export { CURSOR_AGENT_CLI_VERSION };

const VERSION_ID_RE = /^\d{4}\.\d{2}\.\d{2}-[0-9a-f]+$/;
const CACHE_TTL_MS = 60 * 60 * 1000;

let cachedVersion: string | null = null;
let cachedAt = 0;

export function isCursorAgentCliVersionId(value: string): boolean {
  return VERSION_ID_RE.test(value);
}

export function formatCursorAgentClientVersion(id: string): string {
  return `cli-${id}`;
}

/** Extract `versions/<id>` from a resolved agent binary path. */
export function extractVersionIdFromResolvedPath(resolvedPath: string): string | null {
  const parts = resolvedPath.split(/[/\\]/);
  const versionsIdx = parts.lastIndexOf("versions");
  if (versionsIdx < 0 || versionsIdx + 1 >= parts.length) return null;
  const id = parts[versionsIdx + 1];
  return isCursorAgentCliVersionId(id) ? id : null;
}

export function newestVersionInDir(versionsDir: string): string | null {
  try {
    if (!existsSync(versionsDir)) return null;
    // Prefer newest mtime (oakimov), break ties with lexicographic id.
    let newest: { name: string; mtimeMs: number } | null = null;
    for (const name of readdirSync(versionsDir)) {
      if (!isCursorAgentCliVersionId(name)) continue;
      try {
        const st = lstatSync(join(versionsDir, name));
        if (!st.isDirectory()) continue;
        const mtimeMs = st.mtimeMs;
        if (
          !newest ||
          mtimeMs > newest.mtimeMs ||
          (mtimeMs === newest.mtimeMs && name > newest.name)
        ) {
          newest = { name, mtimeMs };
        }
      } catch {
        /* skip vanished entries */
      }
    }
    return newest?.name ?? null;
  } catch {
    return null;
  }
}

function versionFromShim(shimPath: string): string | null {
  try {
    if (!existsSync(shimPath)) return null;
    const resolved = realpathSync(shimPath);
    return extractVersionIdFromResolvedPath(resolved);
  } catch {
    return null;
  }
}

function defaultVersionsDir(home: string): string {
  if (process.platform === "win32") {
    const localAppData = process.env.LOCALAPPDATA || join(home, "AppData", "Local");
    return join(localAppData, "cursor-agent", "versions");
  }
  return join(home, ".local", "share", "cursor-agent", "versions");
}

/**
 * Detect an installed Agent CLI build id from the filesystem.
 * @param home - injectable home for tests (defaults to os.homedir())
 */
export function detectCursorAgentCliVersionFromFs(home: string = homedir()): string | null {
  const localBin = join(home, ".local", "bin");
  for (const name of ["agent", "cursor-agent"]) {
    const fromShim = versionFromShim(join(localBin, name));
    if (fromShim) return fromShim;
  }

  const dataDir = process.env.CURSOR_DATA_DIR;
  const versionsDir = dataDir ? join(dataDir, "versions") : defaultVersionsDir(home);
  return newestVersionInDir(versionsDir);
}

/**
 * Resolve CLI build id synchronously for request headers.
 * Env, then local FS, then the pin.
 */
export function getCursorAgentCliVersion(): string {
  const now = Date.now();
  if (cachedVersion && now - cachedAt < CACHE_TTL_MS) {
    return cachedVersion;
  }

  const fromEnv = process.env.CURSOR_AGENT_CLI_VERSION?.trim();
  if (fromEnv && isCursorAgentCliVersionId(fromEnv)) {
    cachedVersion = fromEnv;
    cachedAt = now;
    return cachedVersion;
  }

  const home = process.env.HOME || process.env.USERPROFILE || homedir();
  const fromFs = detectCursorAgentCliVersionFromFs(home);
  if (fromFs) {
    cachedVersion = fromFs;
    cachedAt = now;
    return cachedVersion;
  }

  return CURSOR_AGENT_CLI_VERSION;
}

/** Exposed for testing: reset the in-memory cache. */
export function resetCursorAgentCliVersionCache(): void {
  cachedVersion = null;
  cachedAt = 0;
}
