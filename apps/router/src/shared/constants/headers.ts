// FreeRoute: these are outbound-only telemetry headers set on API responses.
// Renamed from X-FreeRoute-* to X-FreeRoute-* — no client ever sends these as
// input, so there is no old name to keep accepting.
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
