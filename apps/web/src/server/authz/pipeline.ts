import { randomUUID } from "node:crypto";

import { NextResponse, type NextRequest } from "next/server";

import { classifyRoute } from "./classify";
import { validateBrowserMutationOrigin, isMutationMethod } from "./csrf";
import {
  AUTHZ_HEADER_PEER_LOCALITY,
  AUTHZ_HEADER_REQUEST_ID,
  AUTHZ_HEADER_ROUTE_CLASS,
  AUTHZ_TRUSTED_HEADERS,
} from "./headers";
import { classifyRequestPeerLocality } from "./peerStamp";
import { isLocalOnlyPath } from "./routeGuard";
import type { AuthOutcome, RouteClassification } from "./types";

export interface AuthzPipelineOptions {
  enforce?: boolean;
}

function generateRequestId(): string {
  return randomUUID();
}

function isSameOriginOrLoopbackOrigin(origin: string | null, request: NextRequest): boolean {
  if (!origin) return false;
  if (origin === request.nextUrl.origin) return true;
  try {
    const { hostname } = new URL(origin);
    return ["localhost", "127.0.0.1", "::1"].includes(hostname);
  } catch {
    return false;
  }
}

function applyCorsHeaders(response: NextResponse, request: NextRequest): void {
  const origin = request.headers.get("origin");
  if (!isSameOriginOrLoopbackOrigin(origin, request)) return;

  response.headers.set("Access-Control-Allow-Origin", origin!);
  response.headers.set("Vary", "Origin");
  response.headers.set("Access-Control-Allow-Methods", "GET,HEAD,POST,PUT,PATCH,DELETE,OPTIONS");
  response.headers.set(
    "Access-Control-Allow-Headers",
    request.headers.get("access-control-request-headers") ?? "Content-Type, Authorization"
  );
  response.headers.set("Access-Control-Max-Age", "600");
}

function rejectionResponse(
  outcome: Extract<AuthOutcome, { allow: false }>,
  classification: RouteClassification,
  requestId: string
): NextResponse {
  const response = NextResponse.json(
    {
      error: {
        code: outcome.code,
        message: outcome.message,
        correlation_id: requestId,
      },
    },
    { status: outcome.status }
  );
  response.headers.set(AUTHZ_HEADER_REQUEST_ID, requestId);
  response.headers.set(AUTHZ_HEADER_ROUTE_CLASS, classification.routeClass);
  return response;
}

function stampResponse(
  response: NextResponse,
  requestId: string,
  classification: RouteClassification,
  peerLocality: "loopback" | "remote"
): NextResponse {
  response.headers.set(AUTHZ_HEADER_REQUEST_ID, requestId);
  response.headers.set(AUTHZ_HEADER_ROUTE_CLASS, classification.routeClass);
  response.headers.set(AUTHZ_HEADER_PEER_LOCALITY, peerLocality);
  return response;
}

export async function runAuthzPipeline(
  request: NextRequest,
  options: AuthzPipelineOptions = {}
): Promise<NextResponse> {
  const enforce = options.enforce ?? true;
  const { pathname } = request.nextUrl;
  const method = request.method.toUpperCase();

  const requestHeaders = new Headers(request.headers);
  for (const trusted of AUTHZ_TRUSTED_HEADERS) {
    requestHeaders.delete(trusted);
  }

  const requestId = generateRequestId();
  const classification = classifyRoute(pathname, method);
  const checkPath = pathname.startsWith("/ov-api") ? pathname : classification.normalizedPath;

  if (enforce && isLocalOnlyPath(checkPath)) {
    const peerLocality = classifyRequestPeerLocality(request);
    if (peerLocality !== "loopback") {
      const blocked = rejectionResponse(
        {
          allow: false,
          status: 403,
          code: "LOCAL_ONLY",
          message: "This route is only available from loopback.",
        },
        classification,
        requestId
      );
      applyCorsHeaders(blocked, request);
      return stampResponse(blocked, requestId, classification, peerLocality);
    }
  }

  if (method === "OPTIONS") {
    const preflight = new NextResponse(null, { status: 204 });
    applyCorsHeaders(preflight, request);
    return stampResponse(
      preflight,
      requestId,
      classification,
      classifyRequestPeerLocality(request)
    );
  }

  if (enforce && isMutationMethod(method) && !validateBrowserMutationOrigin(request)) {
    const rejected = rejectionResponse(
      {
        allow: false,
        status: 403,
        code: "INVALID_ORIGIN",
        message:
          "Mutation requests must include a loopback or same-origin Origin/Referer header.",
      },
      classification,
      requestId
    );
    applyCorsHeaders(rejected, request);
    return stampResponse(
      rejected,
      requestId,
      classification,
      classifyRequestPeerLocality(request)
    );
  }

  const peerLocality = classifyRequestPeerLocality(request);
  requestHeaders.set(AUTHZ_HEADER_ROUTE_CLASS, classification.routeClass);
  requestHeaders.set(AUTHZ_HEADER_REQUEST_ID, requestId);
  requestHeaders.set(AUTHZ_HEADER_PEER_LOCALITY, peerLocality);

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  applyCorsHeaders(response, request);
  return stampResponse(response, requestId, classification, peerLocality);
}
