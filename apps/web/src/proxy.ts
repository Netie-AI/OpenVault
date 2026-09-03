import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import { runAuthzPipeline } from "./server/authz/pipeline";

/**
 * Next 16 middleware entry (`src/proxy.ts`).
 *
 * CRITICAL: only enforce authz on `/ov-api/*`. Running
 * `NextResponse.next({ request: { headers } })` on document/RSC navigations
 * rewrites request headers and prevents client hydration (SSR HTML paints,
 * React never attaches — Detect stuck on DEMO forever).
 */
export async function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  if (!pathname.startsWith("/ov-api")) {
    return NextResponse.next();
  }
  return runAuthzPipeline(request, { enforce: true });
}

export const config = {
  matcher: ["/ov-api", "/ov-api/:path*"],
};
