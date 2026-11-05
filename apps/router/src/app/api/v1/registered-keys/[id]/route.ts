import { NextResponse } from "next/server";
import { requireManagementAuth } from "@/lib/api/requireManagementAuth";
import { getRegisteredKey, revokeRegisteredKey } from "@/lib/db/registeredKeys";

// ─── GET /api/v1/registered-keys/[id] ────────────────────────────────────────

export async function GET(request: Request, { params }: { params: Promise<{ id: string }> }) {
  // Management scope, not just any valid token: an OpenVault-issued client token
  // (scope self:usage) must not change limits or issue and revoke registered keys.
  const denied = await requireManagementAuth(request, { invalidApiKeyStatus: 401 });
  if (denied) return denied;

  const resolvedParams = await params;
  const key = getRegisteredKey(resolvedParams.id);
  if (!key) {
    return NextResponse.json({ error: "Key not found" }, { status: 404 });
  }

  return NextResponse.json({ key });
}

// ─── DELETE /api/v1/registered-keys/[id] ─────────────────────────────────────

export async function DELETE(request: Request, { params }: { params: Promise<{ id: string }> }) {
  // Management scope, not just any valid token: an OpenVault-issued client token
  // (scope self:usage) must not change limits or issue and revoke registered keys.
  const denied = await requireManagementAuth(request, { invalidApiKeyStatus: 401 });
  if (denied) return denied;

  const resolvedParams = await params;
  const revoked = revokeRegisteredKey(resolvedParams.id);
  if (!revoked) {
    return NextResponse.json({ error: "Key not found or already revoked" }, { status: 404 });
  }

  return NextResponse.json({
    success: true,
    id: resolvedParams.id,
    revokedAt: new Date().toISOString(),
  });
}
