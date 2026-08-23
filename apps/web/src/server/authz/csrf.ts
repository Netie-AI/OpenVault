import type { NextRequest } from "next/server";

import { isLoopbackHost } from "./routeGuard";

const MUTATION_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export function isMutationMethod(method: string): boolean {
  return MUTATION_METHODS.has(method.toUpperCase());
}

function parseOriginLike(value: string | null): string | null {
  if (!value) return null;
  const trimmed = value.trim();
  if (!trimmed) return null;

  try {
    if (trimmed.startsWith("http://") || trimmed.startsWith("https://")) {
      return new URL(trimmed).origin;
    }
  } catch {
    return null;
  }

  return null;
}

function originFromReferer(referer: string | null): string | null {
  if (!referer) return null;
  try {
    return new URL(referer).origin;
  } catch {
    return null;
  }
}

function isAllowedOrigin(origin: string, request: NextRequest): boolean {
  if (origin === request.nextUrl.origin) return true;

  try {
    const url = new URL(origin);
    return isLoopbackHost(url.hostname);
  } catch {
    return false;
  }
}

/**
 * Browser mutations must carry Origin or Referer from loopback or same-origin.
 * No JWT / CSRF token — local-desktop policy only.
 */
export function validateBrowserMutationOrigin(request: NextRequest): boolean {
  const origin =
    parseOriginLike(request.headers.get("origin")) ??
    originFromReferer(request.headers.get("referer"));

  if (!origin) return false;
  return isAllowedOrigin(origin, request);
}
