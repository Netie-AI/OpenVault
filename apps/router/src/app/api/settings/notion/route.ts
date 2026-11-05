import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { isAuthenticated } from "@/shared/utils/apiAuth";
import { clearNotionToken } from "@/lib/db/notion";
import {
  KeysManagedByOpenVaultError,
  isOpenVaultKeyVaultError,
  openVaultErrorResponse,
  resolveProviderApiKey,
} from "@/lib/netie/keyvault";
import { netieErrorResponse } from "@/lib/netie/providerGuards";
import { sanitizeErrorMessage } from "@omniroute/open-sse/utils/error";

// FreeRoute: the Notion integration token is stored in OpenVault (provider
// "notion"), never in this database. POST refuses a token with 501
// keys_managed_by_openvault before anything is written; DELETE still clears a
// copy an older build left behind.

const setTokenSchema = z
  .object({
    token: z.string().min(1).max(500),
  })
  .strict();

export async function GET(request: NextRequest) {
  if (!(await isAuthenticated(request))) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  try {
    const hasToken = Boolean(await resolveProviderApiKey("notion"));
    return NextResponse.json({ connected: hasToken, hasToken });
  } catch (error) {
    if (isOpenVaultKeyVaultError(error)) return openVaultErrorResponse(error);
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
    return NextResponse.json(
      { error: "Missing or invalid token", details: parsed.error.issues },
      { status: 400 }
    );
  }

  // A well-formed token is never stored here, and never echoed back.
  return (
    netieErrorResponse(
      new KeysManagedByOpenVaultError(
        ["token"],
        "The Notion integration token is stored in OpenVault. Add it at " +
          "http://127.0.0.1:3010/keys with provider notion."
      )
    ) ?? NextResponse.json({ error: "keys_managed_by_openvault" }, { status: 501 })
  );
}

export async function DELETE(request: NextRequest) {
  if (!(await isAuthenticated(request))) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  try {
    clearNotionToken();
    return NextResponse.json({
      connected: false,
      message: "Notion integration disconnected",
    });
  } catch (error) {
    return NextResponse.json({ error: sanitizeErrorMessage(error) }, { status: 500 });
  }
}
