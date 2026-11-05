import {
  netieErrorResponse,
  redactProviderNode,
  rejectCredentialHeaders,
} from "@/lib/netie/providerGuards";
import { NextResponse } from "next/server";
import { createProviderNode, getProviderNodes } from "@/models";
import { getProviderNodesCount } from "@/lib/db/providers/nodes";
import {
  OPENAI_COMPATIBLE_PREFIX,
  ANTHROPIC_COMPATIBLE_PREFIX,
} from "@/shared/constants/providers";
import { generateId } from "@/shared/utils";
import {
  DISABLED,
  rejectClientImpersonationHeaders,
  renderDisabled,
} from "@omniroute/open-sse/netie/policy.ts";
import { createProviderNodeSchema, paginationSchema } from "@/shared/validation/schemas";
import { isValidationFailure, validateBody } from "@/shared/validation/helpers";
import { validateProviderNodeBaseUrl } from "./urlGuard";

const OPENAI_COMPATIBLE_DEFAULTS = {
  baseUrl: "https://api.openai.com/v1",
};

const ANTHROPIC_COMPATIBLE_DEFAULTS = {
  baseUrl: "https://api.anthropic.com/v1",
};

async function registerMoonshotFetchersForCreatedNode(node: {
  id?: unknown;
  prefix?: unknown;
  baseUrl?: unknown;
}): Promise<void> {
  try {
    const { registerMoonshotFetchersForNodes } = await import(
      "@omniroute/open-sse/services/moonshotQuotaFetcher.ts"
    );
    registerMoonshotFetchersForNodes([
      {
        id: typeof node.id === "string" ? node.id : null,
        prefix: typeof node.prefix === "string" ? node.prefix : null,
        baseUrl: typeof node.baseUrl === "string" ? node.baseUrl : null,
      },
    ]);
  } catch (error) {
    console.warn("Moonshot fetcher register after node create skipped:", error);
  }
}

function sanitizeAnthropicBaseUrl(baseUrl: string) {
  return (baseUrl || "")
    .trim()
    .replace(/\/$/, "")
    .replace(/\/messages(?:\?[^#]*)?$/i, "");
}

export async function GET(request?: Request) {
  try {
    const url = new URL(request?.url ?? "http://localhost/api/provider-nodes");
    const raw = {
      offset: url.searchParams.get("offset") || undefined,
      limit: url.searchParams.get("limit") || undefined,
    };
    const validation = validateBody(paginationSchema, raw);
    if (isValidationFailure(validation)) {
      return NextResponse.json({ error: validation.error }, { status: 400 });
    }
    const { limit: parsedLimit, offset: parsedOffset } = validation.data;
    const limit =
      Number.isInteger(parsedLimit) && parsedLimit && parsedLimit > 0 ? parsedLimit : undefined;
    const offset =
      Number.isInteger(parsedOffset) && parsedOffset && parsedOffset > 0 ? parsedOffset : 0;

    const total = getProviderNodesCount();
    const nodes = await getProviderNodes({}, limit, offset);
    return NextResponse.json({
      // Header values are never echoed: names only.
      nodes: nodes.map((node) => redactProviderNode(node)),
      total,
      // FreeRoute: the CC Compatible node type is never offered (tls_fingerprint_stealth_disabled).
      ccCompatibleProviderEnabled: false,
    });
  } catch (error) {
    console.log("Error fetching provider nodes:", error);
    return NextResponse.json({ error: "Failed to fetch provider nodes" }, { status: 500 });
  }
}

// POST /api/provider-nodes - Create provider node
export async function POST(request) {
  let rawBody;
  try {
    rawBody = await request.json();
  } catch {
    return NextResponse.json(
      {
        error: {
          message: "Invalid request",
          details: [{ field: "body", message: "Invalid JSON body" }],
        },
      },
      { status: 400 }
    );
  }

  // FreeRoute: provider presets are not offered. The only one was VibeProxy, a local
  // proxy for a user's Claude, ChatGPT and Gemini subscription logins, which is the
  // pooling the CLIProxyAPI and Dario sidecars were removed for.
  const requestedPreset = (rawBody as { preset?: unknown } | null)?.preset;
  if (requestedPreset !== undefined && requestedPreset !== null && requestedPreset !== "") {
    return renderDisabled(DISABLED.consumerSubscription);
  }

  try {
    const validation = validateBody(createProviderNodeSchema, rawBody);
    if (isValidationFailure(validation)) {
      return NextResponse.json({ error: validation.error }, { status: 400 });
    }
    const {
      name,
      prefix,
      apiType,
      baseUrl,
      type,
      compatMode,
      chatPath,
      modelsPath,
      customHeaders,
      iconUrl,
      dailyQuotaResetTimezone,
      dailyQuotaResetHour,
    } = validation.data;

    // Provider keys live in OpenVault: a header that carries one, under any name, is refused.
    const headerRefusal = rejectCredentialHeaders(customHeaders);
    if (headerRefusal) return headerRefusal;
    const identityRefusal = rejectClientImpersonationHeaders(customHeaders);
    if (identityRefusal) return identityRefusal;

    // Determine type
    const nodeType = type || "openai-compatible";

    if (nodeType === "openai-compatible") {
      const resolvedName = (name || "").trim();
      const resolvedPrefix = (prefix || "").trim();
      const resolvedBaseUrl = (baseUrl || OPENAI_COMPATIBLE_DEFAULTS.baseUrl).trim();
      const baseUrlError = validateProviderNodeBaseUrl(resolvedBaseUrl);
      if (baseUrlError) return baseUrlError;

      const node = await createProviderNode({
        id: `${OPENAI_COMPATIBLE_PREFIX}${apiType}-${generateId()}`,
        type: "openai-compatible",
        prefix: resolvedPrefix,
        apiType,
        baseUrl: resolvedBaseUrl,
        name: resolvedName,
        chatPath: chatPath || null,
        modelsPath: modelsPath || null,
        iconUrl: iconUrl?.trim() || null,
        customHeaders: customHeaders || null,
        dailyQuotaResetTimezone: dailyQuotaResetTimezone?.trim() || null,
        dailyQuotaResetHour:
          dailyQuotaResetHour === 0 || dailyQuotaResetHour != null ? dailyQuotaResetHour : null,
      });
      await registerMoonshotFetchersForCreatedNode(node);
      return NextResponse.json({ node: redactProviderNode(node) }, { status: 201 });
    }

    if (nodeType === "anthropic-compatible") {
      // FreeRoute: the CC Compatible node type impersonates the Claude Code client
      // (open-sse/netie/policy.ts), whatever ENABLE_CC_COMPATIBLE_PROVIDER says.
      if (compatMode === "cc") {
        return renderDisabled(DISABLED.tlsStealth);
      }

      const rawBaseUrl = baseUrl || ANTHROPIC_COMPATIBLE_DEFAULTS.baseUrl;
      const sanitizedBaseUrl = sanitizeAnthropicBaseUrl(rawBaseUrl);
      const baseUrlError = validateProviderNodeBaseUrl(sanitizedBaseUrl);
      if (baseUrlError) return baseUrlError;

      const node = await createProviderNode({
        id: `${ANTHROPIC_COMPATIBLE_PREFIX}${generateId()}`,
        type: "anthropic-compatible",
        prefix: (prefix || "").trim(),
        baseUrl: sanitizedBaseUrl,
        name: (name || "").trim(),
        chatPath: chatPath || null,
        modelsPath: modelsPath || null,
        iconUrl: iconUrl?.trim() || null,
        customHeaders: customHeaders || null,
        dailyQuotaResetTimezone: dailyQuotaResetTimezone?.trim() || null,
        dailyQuotaResetHour:
          dailyQuotaResetHour === 0 || dailyQuotaResetHour != null ? dailyQuotaResetHour : null,
      });
      await registerMoonshotFetchersForCreatedNode(node);
      return NextResponse.json({ node: redactProviderNode(node) }, { status: 201 });
    }

    return NextResponse.json({ error: "Invalid provider node type" }, { status: 400 });
  } catch (error) {
    const named = netieErrorResponse(error);
    if (named) return named;
    console.log("Error creating provider node:", error);
    return NextResponse.json({ error: "Failed to create provider node" }, { status: 500 });
  }
}
