import { NextResponse } from "next/server";
import {
  getUpstreamProxyConfig,
  upsertUpstreamProxyConfig,
  deleteUpstreamProxyConfig,
} from "@/lib/db/upstreamProxy";
import { DISABLED, renderDisabled } from "@omniroute/open-sse/netie/policy.ts";

import { z } from "zod";
import { validateBody, isValidationFailure } from "@/shared/validation/helpers";

const upstreamProxySchema = z.object({
  // "dario" (#dario) is a new direct-passthrough mode alongside "cliproxyapi".
  mode: z.enum(["native", "cliproxyapi", "dario", "fallback"]).default("native"),
  enabled: z.boolean().optional().default(true),
  // Retry-leg backend for mode="fallback"; defaults to "cliproxyapi" so any
  // existing fallback config is unchanged when the field is omitted.
  fallbackBackend: z.enum(["cliproxyapi", "dario"]).optional(),
});

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ providerId: string }> }
) {
  const { providerId } = await params;
  if (!providerId) {
    return NextResponse.json({ error: "providerId required" }, { status: 400 });
  }
  const config = await getUpstreamProxyConfig(providerId);
  if (!config) {
    return NextResponse.json({ enabled: false, mode: "native", fallbackBackend: "cliproxyapi" });
  }
  return NextResponse.json(config);
}

export async function PUT(
  request: Request,
  { params }: { params: Promise<{ providerId: string }> }
) {
  const { providerId } = await params;
  if (!providerId) {
    return NextResponse.json({ error: "providerId required" }, { status: 400 });
  }

  const rawBody = await request.json();
  const validation = validateBody(upstreamProxySchema, rawBody);
  if (isValidationFailure(validation)) {
    return NextResponse.json(validation.error, { status: 400 });
  }

  const { mode, enabled, fallbackBackend } = validation.data;

  // FreeRoute: the only backends behind a non-native mode are the CLIProxyAPI and
  // Dario Claude-subscription pooling sidecars, both hard-disabled
  // (open-sse/netie/policy.ts). Only "native" can be saved.
  if (mode !== "native" || fallbackBackend !== undefined) {
    return renderDisabled(DISABLED.consumerSubscription);
  }

  // `mode` is "native" here and no fallback backend is set, so this only stores
  // that a provider goes straight to its own upstream.
  const config = await upsertUpstreamProxyConfig({ providerId, mode, enabled });

  return NextResponse.json(config);
}

export async function DELETE(
  _request: Request,
  { params }: { params: Promise<{ providerId: string }> }
) {
  const { providerId } = await params;
  if (!providerId) {
    return NextResponse.json({ error: "providerId required" }, { status: 400 });
  }
  const deleted = await deleteUpstreamProxyConfig(providerId);
  return NextResponse.json({ deleted });
}
