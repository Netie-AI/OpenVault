import { NextResponse } from "next/server";
import { getApiKeys, getApiKeysCount } from "@/lib/db/apiKeys";
import { isApiKeyRevealEnabled, maskStoredApiKey } from "@/lib/apiKeyExposure";
import { requireManagementAuth } from "@/lib/api/requireManagementAuth";
import { renderClientKeysManagedByOpenVault } from "@omniroute/open-sse/netie/policy.ts";
import * as log from "@/sse/utils/logger";

function parsePagination(request: Request) {
  const url = new URL(request.url);
  const limitValue = url.searchParams.get("limit");
  const offsetValue = url.searchParams.get("offset");

  const parsedLimit = limitValue ? Number.parseInt(limitValue, 10) : undefined;
  const parsedOffset = offsetValue ? Number.parseInt(offsetValue, 10) : 0;

  const limit =
    Number.isInteger(parsedLimit) && parsedLimit && parsedLimit > 0 ? parsedLimit : null;
  const offset = Number.isInteger(parsedOffset) && parsedOffset > 0 ? parsedOffset : 0;

  return { limit, offset };
}

// GET /api/keys - List API keys
export async function GET(request: Request) {
  const authError = await requireManagementAuth(request);
  if (authError) return authError;

  try {
    const { limit, offset } = parsePagination(request);
    const dbLimit = limit ?? undefined;
    const total = getApiKeysCount();
    const keys = await getApiKeys(dbLimit, offset);
    const maskedKeys = keys.map((k) => ({
      ...k,
      key: maskStoredApiKey(k.key),
    }));

    return NextResponse.json({
      keys: maskedKeys,
      total,
      allowKeyReveal: isApiKeyRevealEnabled(),
    });
  } catch (error) {
    log.error("keys", "Error fetching keys", error);
    return NextResponse.json({ error: "Failed to fetch keys" }, { status: 500 });
  }
}

// POST /api/keys - Create new API key
//
// FreeRoute: router-side inbound-client keys are minted by OpenVault
// (POST http://127.0.0.1:5000/api/apikeys, surfaced at
// http://127.0.0.1:3010/keys) — see src/lib/netie/keyvault.ts and
// validateApiKey()/getApiKeyMetadata() in src/lib/db/apiKeys.ts, which accept
// an OpenVault-verified token the same way they already accept the env key.
export async function POST(request: Request) {
  const authError = await requireManagementAuth(request);
  if (authError) return authError;
  return renderClientKeysManagedByOpenVault();
}
