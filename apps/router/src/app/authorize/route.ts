import { DISABLED, renderDisabled } from "@omniroute/open-sse/netie/policy.ts";

/**
 * GET /authorize
 *
 * Upstream: the loopback callback for the Trae SOLO desktop OAuth flow. Trae's
 * authorize server packs the whole credential set into the query string, and
 * the handler persisted it as a pooled Trae subscription connection, with no
 * auth (the path is outside /api, so the request gate never saw it).
 *
 * FreeRoute: consumer subscription pooling is not shipped. The route answers
 * the named 501 and never parses or stores the query. src/proxy.ts also
 * matches /authorize and returns the same 501 before this handler runs.
 */
export async function GET(): Promise<Response> {
  return renderDisabled(DISABLED.consumerSubscription);
}
