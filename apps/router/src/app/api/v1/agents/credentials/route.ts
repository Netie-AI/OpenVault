import { NextRequest, NextResponse } from "next/server";
import { listCloudAgentCredentials } from "@/lib/cloudAgent/credentials";
import {
  getCloudAgentCorsHeaders,
  requireCloudAgentManagementAuth,
  withCloudAgentCors,
} from "@/lib/cloudAgent/api";
import { netieErrorResponse } from "@/lib/netie/providerGuards";
import { renderKeysManagedByOpenVault } from "@omniroute/open-sse/netie/policy.ts";
import pino from "pino";
import { sanitizeErrorMessage } from "@omniroute/open-sse/utils/error";

const logger = pino({ name: "cloud-agents-credentials-api" });

export async function OPTIONS(request: NextRequest) {
  return new NextResponse(null, { headers: getCloudAgentCorsHeaders(request) });
}

export async function GET(request: NextRequest) {
  try {
    const authError = await requireCloudAgentManagementAuth(request);
    if (authError) return authError;

    const data = await listCloudAgentCredentials();

    return NextResponse.json({ data }, { headers: getCloudAgentCorsHeaders(request) });
  } catch (error) {
    const named = netieErrorResponse(error);
    if (named) return withCloudAgentCors(named, request);
    logger.error({ err: error }, "Failed to list cloud agent credentials");
    return NextResponse.json(
      {
        error:
          sanitizeErrorMessage(error instanceof Error ? error.message : "Unknown error") ||
          "Internal server error",
      },
      { status: 500, headers: getCloudAgentCorsHeaders(request) }
    );
  }
}

// FreeRoute: a cloud agent API key is never saved here. It is added in OpenVault
// (http://127.0.0.1:3010/keys) and read from there when the agent is called.
// The management auth check still runs first, so only a signed-in operator sees
// the answer. The body is not read, validated or stored.
export async function POST(request: NextRequest) {
  const authError = await requireCloudAgentManagementAuth(request);
  if (authError) return authError;

  return withCloudAgentCors(renderKeysManagedByOpenVault(), request);
}
