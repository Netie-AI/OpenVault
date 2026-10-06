import { NextResponse } from "next/server";
import { getProviderConnections, updateProviderConnection } from "@/models";
import { requireManagementAuth } from "@/lib/api/requireManagementAuth";
import { cloudCredentialUpdateSchema } from "@/shared/validation/schemas";
import { isValidationFailure, validateBody } from "@/shared/validation/helpers";
import { classifyProviderId, renderDisabled, renderKeysManagedByOpenVault } from "@omniroute/open-sse/netie/policy.ts";
import { netieErrorResponse } from "@/lib/netie/providerGuards";

// Update provider credentials (for cloud token refresh)
export async function PUT(request: Request) {
  const authError = await requireManagementAuth(request, {
    alwaysRequireAuth: true,
    invalidApiKeyStatus: 401,
  });
  if (authError) return authError;

  let rawBody;
  try {
    rawBody = await request.json();
  } catch {
    return NextResponse.json(
      { error: { message: "Invalid request", details: [{ field: "body", message: "Invalid JSON body" }] } },
      { status: 400 }
    );
  }

  try {
    const validation = validateBody(cloudCredentialUpdateSchema, rawBody);
    if (isValidationFailure(validation)) {
      return NextResponse.json({ error: validation.error }, { status: 400 });
    }
    const { provider, credentials } = validation.data;

    // FreeRoute: this route writes access and refresh tokens into a local
    // connection. For a subscription or browser-session provider that is the
    // pooling feature itself; for anything else it stores a provider credential
    // outside OpenVault. Either way it answers the named 501.
    if (credentials.accessToken || credentials.refreshToken) {
      const disabledCode = classifyProviderId(provider);
      return disabledCode ? renderDisabled(disabledCode) : renderKeysManagedByOpenVault();
    }

    // Find active connection for provider
    const connections = await getProviderConnections({ provider, isActive: true });
    const connection = connections[0];

    if (!connection) {
      return NextResponse.json(
        { error: `No active connection found for provider: ${provider}` },
        { status: 404 }
      );
    }

    // Update credentials
    const updateData: Record<string, unknown> = {};
    if (credentials.accessToken) {
      updateData.accessToken = credentials.accessToken;
    }
    if (credentials.refreshToken) {
      updateData.refreshToken = credentials.refreshToken;
    }
    if (credentials.expiresIn) {
      updateData.expiresAt = new Date(Date.now() + credentials.expiresIn * 1000).toISOString();
    }

    const connectionId = typeof connection.id === "string" ? connection.id : null;
    if (!connectionId) {
      return NextResponse.json({ error: "Invalid provider connection ID" }, { status: 500 });
    }
    await updateProviderConnection(connectionId, updateData);

    return NextResponse.json({
      success: true,
      message: `Credentials updated for provider: ${provider}`,
    });
  } catch (error) {
    const named = netieErrorResponse(error);
    if (named) return named;
    console.log("Update credentials error:", error);
    return NextResponse.json({ error: "Failed to update credentials" }, { status: 500 });
  }
}
