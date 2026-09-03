/**
 * Local-desktop authz types for the Next.js proxy middleware.
 *
 * Three route classes:
 *   PUBLIC      — static assets and safe read-only surfaces.
 *   CLIENT_API  — proxied backend reads and /v1 model-serving routes.
 *   MANAGEMENT  — UI pages and mutating management API calls.
 */

export type RouteClass = "PUBLIC" | "CLIENT_API" | "MANAGEMENT";

export type ClassificationReason =
  | "static_asset"
  | "public_prefix"
  | "client_api_v1"
  | "client_api_read"
  | "management_api_mutation"
  | "management_page"
  | "fallback_management";

export interface RouteClassification {
  routeClass: RouteClass;
  reason: ClassificationReason;
  /** Path used for policy checks (may strip the /ov-api rewrite prefix). */
  normalizedPath: string;
}

export type AuthOutcome =
  | { allow: true }
  | { allow: false; status: number; code: string; message: string };
