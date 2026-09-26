/**
 * FreeRoute: update-source discovery is disabled.
 *
 * Upstream OmniRoute checked npm, the npm registry, and GitHub releases for a
 * newer OmniRoute version to power a dashboard "Update Available" banner and
 * an in-app updater. FreeRoute ships as part of OpenVault and must not check,
 * download, or display upstream OmniRoute releases (see PRODUCT_ROLES.md) —
 * so every lookup below is a no-op: no network call, no `npm`/`execFile`
 * invocation, and no cache to keep warm. `GET/POST /api/system/version`
 * (src/app/api/system/version/route.ts) returns the named 501
 * `upstream_updates_disabled` before any of this would even run; the no-op
 * exports remain so the handful of other call sites that check "is there an
 * update" keep compiling and get an honest "no update source configured"
 * instead of a broken import.
 */
import { createLogger } from "@/shared/utils/logger";

const log = createLogger("system/versionCheck");
let noSourceWarningLogged = false;

function warnNoUpdateSource(): void {
  if (noSourceWarningLogged) return;
  noSourceWarningLogged = true;
  log.warn(
    "no update source configured — FreeRoute does not check upstream OmniRoute releases"
  );
}

// The pure semver helpers live in `./versionCompare` (dependency-free) so
// client-reachable modules can import them without pulling this file's
// server-only imports into the browser bundle. Re-exported here for
// back-compat with existing server-side importers.
export { normalizeVersion, isNewer } from "./versionCompare";

/** No-op: FreeRoute does not shell out to `npm info` to check for updates. */
export async function getLatestVersionFromNpmCli(): Promise<string | null> {
  warnNoUpdateSource();
  return null;
}

/** No-op: FreeRoute does not query the npm registry for updates. */
export async function getLatestVersionFromRegistry(): Promise<string | null> {
  warnNoUpdateSource();
  return null;
}

/** No-op: FreeRoute does not query upstream OmniRoute's GitHub releases. */
export async function getLatestVersionFromGitHub(): Promise<string | null> {
  warnNoUpdateSource();
  return null;
}

export function clearLatestVersionCache(): void {
  /* no cache to clear — nothing is ever looked up */
}

/** Always resolves `null` — see module header. */
export async function resolveLatestVersionCached(): Promise<string | null> {
  warnNoUpdateSource();
  return null;
}

/** Always resolves `null` — see module header. */
export async function resolveLatestVersion(): Promise<string | null> {
  warnNoUpdateSource();
  return null;
}
