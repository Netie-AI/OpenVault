import type { NextRequest } from "next/server";

import { classifyHostLocality, isLoopbackHost } from "./routeGuard";

export type PeerLocality = "loopback" | "remote";

/**
 * Resolve the best-effort peer IP for middleware (no socket in the Edge/Node
 * middleware runtime). Uses x-forwarded-for first hop, then x-real-ip, then
 * falls back to loopback when Host is local and no forwarding headers exist.
 */
export function resolveRequestPeerIp(request: NextRequest): string | null {
  const xff = request.headers.get("x-forwarded-for");
  if (xff) {
    const first = xff.split(",")[0]?.trim();
    if (first) return first;
  }

  const realIp = request.headers.get("x-real-ip")?.trim();
  if (realIp) return realIp;

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
