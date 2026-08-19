/**
 * LOCAL_ONLY route guard for spawn-capable OpenVault backend surfaces.
 *
 * Verify manually:
 *   npx tsx src/server/authz/routeGuard.test.ts
 */

const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "::1"]);

/**
 * Paths that must only be reachable from loopback, with or without /ov-api.
 *
 * Kept for the explicit spawn-capable surfaces, but it is no longer the whole
 * control — see `isLocalOnlyPath`. An enumerate-forever list means every new
 * custody route is remote-reachable until somebody remembers to add it here,
 * and `/api/keys`, `/api/secrets` and `/api/vault/` had been missing from it
 * the entire time, which is exactly that failure.
 */
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

/**
 * The only backend paths a non-loopback caller may reach.
 *
 * Default-deny, because the failure modes are not symmetric: a missing entry
 * here refuses work somebody can immediately report, while a missing entry in a
 * local-only *denylist* silently exposes custody. Health is here so a container
 * probe still works; mesh/peer routes are here because LAN device discovery is
 * a deliberate feature (DR-0002), not an accident.
 */
export const REMOTE_ALLOWED_API_PREFIXES: ReadonlyArray<string> = [
  "/api/healthz",
  "/api/health",
  "/api/mesh",
  "/api/peers",
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

function matchesPrefix(path: string, prefixes: ReadonlyArray<string>): boolean {
  return prefixes.some((prefix) => path === prefix || path.startsWith(prefix));
}

/** Normalise `/ov-api/api/x` and `/api/x` to one form for allowlist matching. */
function backendPath(path: string): string | null {
  if (path.startsWith("/ov-api/api/")) return path.slice("/ov-api".length);
  if (path.startsWith("/api/")) return path;
  return null;
}

export function isLocalOnlyPath(path: string): boolean {
  if (matchesPrefix(path, LOCAL_ONLY_API_PREFIXES)) return true;

  // Default-deny for everything else that reaches the OpenVault backend. The
  // console proxies /ov-api/* to 127.0.0.1:5000, so FastAPI's own loopback
  // check sees a local peer for every proxied request no matter who sent it --
  // this middleware is the only layer that can still tell them apart.
  const backend = backendPath(path);
  if (backend === null) return false;
  return !matchesPrefix(backend, REMOTE_ALLOWED_API_PREFIXES);
}
