import type { NextRequest } from "next/server";

import { classifyHostLocality, isLoopbackHost } from "./routeGuard";

export type PeerLocality = "loopback" | "remote";

/**
 * Resolve the best-effort peer IP for middleware (no socket in the Edge/Node
 * middleware runtime).
 *
 * x-forwarded-for and x-real-ip are attacker-controlled unless a proxy we trust
 * overwrites them. This used to read the first XFF hop before anything else, so
 * `curl -H "X-Forwarded-For: 127.0.0.1"` from any machine classified as
 * loopback and walked straight through the LOCAL_ONLY guard. They are now
 * ignored unless OPENVAULT_TRUST_PROXY is explicitly set by someone who really
 * does run this behind a proxy that rewrites them.
 */
function trustsForwardedHeaders(): boolean {
  const raw = (process.env.OPENVAULT_TRUST_PROXY ?? "").trim().toLowerCase();
  return raw === "1" || raw === "true" || raw === "yes" || raw === "on";
}

export function resolveRequestPeerIp(request: NextRequest): string | null {
  // Console binds 127.0.0.1 (STATUS #34). Prefer the received URL hostname
  // before XFF: Next injects x-forwarded-for on local /ov-api rewrites, and the
  // old fail-closed path returned null -> peer=remote -> LOCAL_ONLY 403 on
  // /api/keys, so the vault UI painted empty while :5000 still had keys.
  if (isLoopbackHost(request.nextUrl.hostname)) return "127.0.0.1";

  const xff = request.headers.get("x-forwarded-for");
  const realIp = request.headers.get("x-real-ip")?.trim();

  if (trustsForwardedHeaders()) {
    if (xff) {
      const first = xff.split(",")[0]?.trim();
      if (first) return first;
    }
    if (realIp) return realIp;
  } else if (xff || realIp) {
    // Untrusted forwarded headers on a non-loopback Host: fail closed.
    return null;
  }

  const host = request.headers.get("host");
  if (host) {
    const hostname = host.startsWith("[")
      ? host.slice(1, host.indexOf("]"))
      : host.split(":")[0];
    if (isLoopbackHost(hostname)) return "127.0.0.1";
  }

  return null;
}

export function classifyRequestPeerLocality(request: NextRequest): PeerLocality {
  return classifyHostLocality(resolveRequestPeerIp(request));
}
