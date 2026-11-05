import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { isAuthenticated } from "@/shared/utils/apiAuth";
import {
  clearObsidianToken,
  getObsidianConfig,
  getObsidianVaultPath,
  setObsidianVaultPath,
} from "@/lib/db/obsidian";
import { sanitizeErrorMessage } from "@omniroute/open-sse/utils/error";
import { KeysManagedByOpenVaultError, netieErrorResponse } from "@/lib/netie/providerGuards";
import {
  CLOUD_METADATA_BLOCKED_MESSAGE,
  parseAndValidateNonMetadataUrl,
} from "@/shared/network/outboundUrlGuard";

// GHSA-474q-g63r-w4rr: the base URL is an outbound target. Loopback / LAN / Tailscale
// hosts are legitimate (the Local REST API runs next to the vault), cloud-metadata and
// link-local never are — and the check runs here, before any fetch and before the URL
// is persisted for the /api/obsidian/* routes to reuse.
const obsidianBaseUrlSchema = z
  .string()
  .url()
  .refine(
    (value) => {
      try {
        parseAndValidateNonMetadataUrl(value);
        return true;
      } catch {
        return false;
      }
    },
    { message: CLOUD_METADATA_BLOCKED_MESSAGE }
  );

const setTokenSchema = z
  .object({
    token: z.string().min(1).max(5000),
    baseUrl: obsidianBaseUrlSchema.optional(),
  })
  .strict();

export async function GET(request: NextRequest) {
  if (!(await isAuthenticated(request))) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  try {
    const config = getObsidianConfig();
    return NextResponse.json({
      connected: config.connected,
      hasToken: config.token !== null,
      baseUrl: config.baseUrl,
      vaultPath: config.vaultPath,
    });
  } catch (error) {
    return NextResponse.json({ error: sanitizeErrorMessage(error) }, { status: 500 });
  }
}

export async function POST(request: NextRequest) {
  if (!(await isAuthenticated(request))) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  let rawBody: unknown;
  try {
    rawBody = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const parsed = setTokenSchema.safeParse(rawBody);
  if (!parsed.success) {
    const baseUrlIssue = parsed.error.issues.find((issue) => issue.path[0] === "baseUrl");
    return NextResponse.json(
      {
        error: baseUrlIssue
          ? `Invalid baseUrl: ${baseUrlIssue.message}`
          : "Missing or invalid token",
        details: parsed.error.issues,
      },
      { status: 400 }
    );
  }

  // FreeRoute keeps no credentials, and the Obsidian client reads its token from this
  // database only, so saving one is disabled. Nothing is written or echoed back.
  return (
    netieErrorResponse(
      new KeysManagedByOpenVaultError(
        ["token"],
        "The Obsidian API token would be stored in FreeRoute's own database, and FreeRoute does " +
          "not keep credentials. Saving it here is disabled."
      )
    ) ?? NextResponse.json({ error: "keys_managed_by_openvault" }, { status: 501 })
  );
}

export async function DELETE(request: NextRequest) {
  if (!(await isAuthenticated(request))) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  try {
    clearObsidianToken();
    return NextResponse.json({
      connected: false,
      message: "Obsidian integration disconnected",
    });
  } catch (error) {
    return NextResponse.json({ error: sanitizeErrorMessage(error) }, { status: 500 });
  }
}
