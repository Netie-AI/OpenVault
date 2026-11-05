// Copyright (c) 2026 Netie AI. MIT.
//
// Wrapper for client-facing /v1 routes outside handleChat (images, video,
// music, audio, embeddings, rerank, classify, web fetch, session leases, ...).
//
// 1. Before the handler runs, it peeks at the request's `model` (JSON body or
//    multipart form field) and answers the named 501 when the model's
//    "<provider>/" prefix is a subscription or browser-session provider. This
//    also covers providers that need no credential (for example
//    veoaifree-web), which never reach the credential choke point.
// 2. Any NetieDisabledError, keys_managed_by_openvault error or KeyVault error
//    the handler throws (getProviderCredentials and materializeConnection
//    throw them) becomes its named JSON response instead of a generic 500.
//
// The handler still sees the original, unread request.

import { classifyProviderId, renderDisabled } from "@omniroute/open-sse/netie/policy.ts";
import { netieErrorResponse } from "./providerGuards";

type RouteHandler<Ctx> = (request: Request, context: Ctx) => Promise<Response> | Response;

async function peekModel(request: Request): Promise<string | null> {
  if (!["POST", "PUT", "PATCH"].includes(request.method)) return null;
  const contentType = request.headers.get("content-type") || "";
  try {
    if (contentType.includes("application/json")) {
      const body = (await request.clone().json()) as { model?: unknown } | null;
      return typeof body?.model === "string" ? body.model : null;
    }
    if (contentType.includes("multipart/form-data")) {
      const form = await request.clone().formData();
      const model = form.get("model");
      return typeof model === "string" ? model : null;
    }
  } catch {
    // Malformed body: the handler answers that with its own 400.
  }
  return null;
}

/** Named 501 for a "<provider>/<model>" whose provider is classified, else null. */
export function disabledModelResponse(model: unknown): Response | null {
  if (typeof model !== "string") return null;
  const slash = model.indexOf("/");
  if (slash <= 0) return null;
  const code = classifyProviderId(model.slice(0, slash));
  return code ? renderDisabled(code) : null;
}

export function withNetiePolicy<Ctx = unknown>(handler: RouteHandler<Ctx>): RouteHandler<Ctx> {
  return async function netiePolicyHandler(request: Request, context: Ctx): Promise<Response> {
    const disabled = disabledModelResponse(await peekModel(request));
    if (disabled) return disabled;
    try {
      return await handler(request, context);
    } catch (error) {
      const named = netieErrorResponse(error);
      if (named) return named;
      throw error;
    }
  };
}
