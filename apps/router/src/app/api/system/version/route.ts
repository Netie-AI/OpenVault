/**
 * GET/POST /api/system/version — disabled in FreeRoute.
 *
 * Upstream OmniRoute used this route to check npm/GitHub for a newer release
 * and, on POST, to run an in-place `git checkout` + `npm install` + restart.
 * FreeRoute must not check, download, or install upstream OmniRoute releases
 * (see PRODUCT_ROLES.md) — both verbs return the named 501 before touching
 * any of that logic. See src/lib/system/versionCheck.ts for the equivalent
 * no-op on the lookup helpers themselves.
 */
import { NextRequest, NextResponse } from "next/server";
import { isAuthenticated } from "@/shared/utils/apiAuth";
import { renderUpstreamUpdatesDisabled } from "@omniroute/open-sse/netie/policy.ts";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  if (!(await isAuthenticated(req))) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  return renderUpstreamUpdatesDisabled();
}

export async function POST(req: NextRequest) {
  if (!(await isAuthenticated(req))) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  return renderUpstreamUpdatesDisabled();
}
