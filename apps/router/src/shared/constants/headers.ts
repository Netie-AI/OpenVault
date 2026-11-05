// Copyright (c) 2026 Netie AI. MIT.
//
// FreeRoute telemetry and control headers. Every X-FreeRoute-* header is sent
// together with its legacy X-OmniRoute-* twin (same value), and every reader
// accepts either name, so existing clients, plugins and peer instances that
// key on the old name keep working. The twin is the compatibility contract in
// DR-0018 ("X-OmniRoute-* header names kept"); it is not a brand surface.
export const OMNIROUTE_RESPONSE_HEADERS = {
  cache: "X-FreeRoute-Cache",
  cacheHit: "X-FreeRoute-Cache-Hit",
  cacheLatency: "X-FreeRoute-Cache-Latency",
  cacheSimilarity: "X-FreeRoute-Cache-Similarity",
  savingsTokens: "X-FreeRoute-Savings-Tokens",
  compression: "X-FreeRoute-Compression",
  costSaved: "X-FreeRoute-Cost-Saved",
  decision: "X-FreeRoute-Decision",
  fallbackAttempts: "X-FreeRoute-Fallback-Attempts",
  latencyMs: "X-FreeRoute-Latency-Ms",
  model: "X-FreeRoute-Model",
  progress: "X-FreeRoute-Progress",
  provider: "X-FreeRoute-Provider",
  requestId: "X-FreeRoute-Request-Id",
  responseCost: "X-FreeRoute-Response-Cost",
  tokensIn: "X-FreeRoute-Tokens-In",
  tokensOut: "X-FreeRoute-Tokens-Out",
  tokensPerSecond: "X-FreeRoute-Tokens-Per-Second",
  version: "X-FreeRoute-Version",
} as const;

export const FREEROUTE_HEADER_PREFIX = "x-freeroute-";
export const LEGACY_HEADER_PREFIX = "x-omniroute-";

/** Request headers a client may send under either name. */
export const FREEROUTE_REQUEST_HEADERS = {
  connection: "X-FreeRoute-Connection",
  noCache: "X-FreeRoute-No-Cache",
  sessionId: "X-FreeRoute-Session-Id",
  peerTrace: "X-FreeRoute-Peer-Trace",
} as const;

/** Response headers the router sets for combos and callers to read back. */
export const FREEROUTE_ROUTING_RESPONSE_HEADERS = {
  selectedConnectionId: "X-FreeRoute-Selected-Connection-Id",
  sessionId: "X-FreeRoute-Session-Id",
  providerBreaker: "X-FreeRoute-Provider-Breaker",
  providerManifestUrl: "X-FreeRoute-Provider-Manifest-Url",
} as const;

function swapPrefix(name: string, from: string, toLower: string, toTitle: string): string | null {
  if (name.length <= from.length || name.slice(0, from.length).toLowerCase() !== from) return null;
  const rest = name.slice(from.length);
  return (name === name.toLowerCase() ? toLower : toTitle) + rest;
}

/** The legacy X-OmniRoute-* twin of an X-FreeRoute-* name, or null for any other name. */
export function legacyHeaderName(name: string): string | null {
  return swapPrefix(name, FREEROUTE_HEADER_PREFIX, LEGACY_HEADER_PREFIX, "X-OmniRoute-");
}

/** The X-FreeRoute-* name of a legacy X-OmniRoute-* header, or null for any other name. */
export function freerouteHeaderName(name: string): string | null {
  return swapPrefix(name, LEGACY_HEADER_PREFIX, FREEROUTE_HEADER_PREFIX, "X-FreeRoute-");
}

/**
 * Add the legacy twin for every X-FreeRoute-* key of a plain header object.
 * Mutates and returns `headers`. An existing twin is left as it is.
 */
export function withLegacyHeaderTwins<T extends Record<string, string>>(headers: T): T {
  const present = new Set(Object.keys(headers).map((key) => key.toLowerCase()));
  for (const [name, value] of Object.entries(headers)) {
    const twin = legacyHeaderName(name);
    if (twin && !present.has(twin.toLowerCase())) {
      (headers as Record<string, string>)[twin] = value;
      present.add(twin.toLowerCase());
    }
  }
  return headers;
}

/** Set a header on a Headers instance together with its legacy twin. */
export function setHeaderWithTwin(headers: Headers, name: string, value: string): void {
  headers.set(name, value);
  const twin = legacyHeaderName(name);
  if (twin) headers.set(twin, value);
}

export type HeaderSource =
  | Headers
  | { get(name: string): string | null | undefined }
  | Record<string, string | string[] | undefined | null>
  | null
  | undefined;

function readOne(headers: HeaderSource, name: string): string | null {
  if (!headers || typeof headers !== "object") return null;
  const getter = (headers as { get?: unknown }).get;
  if (typeof getter === "function") {
    const value = (getter as (key: string) => string | null | undefined).call(headers, name);
    return typeof value === "string" ? value : null;
  }
  const lower = name.toLowerCase();
  for (const [key, value] of Object.entries(headers as Record<string, unknown>)) {
    if (key.toLowerCase() !== lower) continue;
    if (typeof value === "string") return value;
    if (Array.isArray(value) && typeof value[0] === "string") return value[0];
  }
  return null;
}

/**
 * Read a header under either its X-FreeRoute-* or its legacy X-OmniRoute-* name.
 * Pass either spelling; the FreeRoute name wins when both are present and non-empty.
 */
export function readFreeRouteHeader(headers: HeaderSource, name: string): string | null {
  const freeroute = freerouteHeaderName(name) ?? name;
  const legacy = legacyHeaderName(freeroute);
  const primary = readOne(headers, freeroute);
  if (primary !== null && primary !== "") return primary;
  if (legacy) {
    const secondary = readOne(headers, legacy);
    if (secondary !== null) return secondary;
  }
  return primary;
}

/** The connection id a response says it used, under either header name. */
export function readSelectedConnectionId(
  headers: HeaderSource
): string | undefined {
  return (
    readFreeRouteHeader(headers, FREEROUTE_ROUTING_RESPONSE_HEADERS.selectedConnectionId) ||
    undefined
  );
}
