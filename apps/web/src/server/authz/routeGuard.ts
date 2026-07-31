/**
 * LOCAL_ONLY route guard for spawn-capable OpenVault backend surfaces.
 *
 * Verify manually:
 *   npx tsx src/server/authz/routeGuard.test.ts
 */

const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "::1"]);

/** Paths that must only be reachable from loopback, with or without /ov-api. */
export const LOCAL_ONLY_API_PREFIXES: ReadonlyArray<string> = [
  "/ov-api/api/ship/",
  "/ov-api/api/deploy/",
  "/ov-api/api/control/action",
  "/ov-api/api/openide/invoke",
  "/ov-api/api/engine/candidates/",
  "/ov-api/api/sentinel/",
  "/api/ship/",
  "/api/deploy/",
  "/api/control/action",
  "/api/openide/invoke",
  "/api/engine/candidates/",
  "/api/sentinel/",
];

export function isLoopbackHost(hostHeader: string | null): boolean {
  if (!hostHeader) return false;
  let host = hostHeader.trim();
  if (host.startsWith("[")) {
    const bracketEnd = host.indexOf("]");
    host = bracketEnd >= 0 ? host.slice(1, bracketEnd) : host.slice(1);
  } else if ((host.match(/:/g) || []).length === 1) {
    host = host.split(":")[0];
  }
  host = host.replace(/^::ffff:/i, "");
  return LOOPBACK_HOSTS.has(host.toLowerCase());
}

export function classifyHostLocality(ip: string | null): "loopback" | "remote" {
  if (!ip) return "remote";
  return isLoopbackHost(ip) ? "loopback" : "remote";
}

export function isLocalOnlyPath(path: string): boolean {
  return LOCAL_ONLY_API_PREFIXES.some((prefix) => path === prefix || path.startsWith(prefix));
}
