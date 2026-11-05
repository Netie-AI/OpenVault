/**
 * User-supplied upstream extra headers: names we never forward (Host / hop-by-hop / framing).
 * Changing this list requires syncing: `sanitizeUpstreamHeadersMap` (models.ts), Zod
 * `upstreamHeaderNameSchema` / record refine (schemas.ts), and `upstream-headers-sanitize` tests.
 *
 * The forwarding/IP set (x-forwarded-for, x-real-ip, cf-connecting-ip, forwarded, via, …)
 * is forbidden so the client-origin IP can never be disclosed (or spoofed) to the upstream
 * provider through an operator-set custom upstream header. This mirrors the established
 * scrubbers/denylists already used by the Antigravity (`antigravityHeaderScrub.ts`) and
 * Cursor CLI (`cursorCliProxy.ts`) paths, extended here to cover every provider.
 */
const FORBIDDEN = new Set(
  [
    "host",
    "connection",
    "content-length",
    "keep-alive",
    "proxy-connection",
    // The two RFC 7230 §6.1 hop-by-hop names this list was missing. They belong
    // to the connection between the client and FreeRoute (or its upstream
    // proxy), never to the request FreeRoute makes to the model provider —
    // forwarding `proxy-authorization` hands that proxy credential to the
    // provider. `src/lib/services/reverseProxy.ts` (HOP_BY_HOP),
    // `src/mitm/sanitizeHeaders.ts`, `src/mitm/inspector/httpProxyServer.ts`,
    // `src/mitm/tproxy/tlsCapture.ts` and `src/app/api/openapi/try/route.ts`
    // all already strip them; this list, the canonical one, did not.
    "proxy-authenticate",
    "proxy-authorization",
    "transfer-encoding",
    "te",
    "trailer",
    "upgrade",
    // Origin-IP disclosure: never send the client's forwarding headers upstream.
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-forwarded-port",
    "x-forwarded-server",
    "x-real-ip",
    "cf-connecting-ip",
    "true-client-ip",
    "client-ip",
    "forwarded",
    "via",
  ].map((s) => s.toLowerCase())
);

export function isForbiddenUpstreamHeaderName(name: string): boolean {
  return FORBIDDEN.has(String(name).trim().toLowerCase());
}

/**
 * Auth headers that must come from the connection's credentials, never from
 * operator-set per-provider custom headers. Kept here as the single source of
 * truth so the Zod `customHeadersSchema` (schemas.ts) and the executor's
 * apply-loop (open-sse/executors/default.ts) cannot drift apart.
 */
const FORBIDDEN_AUTH = new Set(
  ["authorization", "x-api-key", "x-goog-api-key", "api-key", "cookie"].map((s) => s.toLowerCase())
);

/**
 * Forbidden for operator-supplied custom headers: the hop-by-hop/framing set
 * PLUS auth headers (owned by the credential layer). Use this in both the
 * validation schema and the executor so there is one canonical denylist.
 */
export function isForbiddenCustomHeaderName(name: string): boolean {
  const n = String(name).trim().toLowerCase();
  return isForbiddenUpstreamHeaderName(n) || FORBIDDEN_AUTH.has(n);
}

// ── Credential-looking header values ──────────────────────────────────────
//
// Provider keys live only in OpenVault, so a header map FreeRoute would store
// (provider-node customHeaders, providerSpecificData.extraHeaders) must not
// carry one. The name denylist above stops Authorization and x-api-key; this
// catches a key put under any other name by what the value looks like.

const CREDENTIAL_SCHEME_RE = /^(?:bearer|basic|token|digest|negotiate)\s+\S{6,}$/i;
const CREDENTIAL_PREFIX_RE =
  /^(?:sk-|sk_|pk_live_|rk_live_|ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|glpat-|xox[abposr]-|xapp-|AIza|AKIA|ASIA|ya29\.|hf_|npm_|pplx-|gsk_|r8_|nvapi-|fw_|ovtok_|ov_)[A-Za-z0-9_.\-]{8,}$/;
const JWT_RE = /^eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*$/;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const TOKEN_CHARS_RE = /^[A-Za-z0-9+/=_.~-]+$/;
const MIN_ENTROPY_LENGTH = 20;
const MIN_ENTROPY_BITS = 3.3;

// A segment of a slug or version string: a lowercase or Capitalized word, digits, or a short
// alphanumeric like v2, 4o. An all-caps run or a long mixed segment is not.
const WORD_SEGMENT_RES = [
  /^[a-z]{1,20}$/,
  /^[A-Z]?[a-z]+(?:[A-Z][a-z]+)*$/,
  /^\d+$/,
  /^\d{1,2}[a-z]{1,2}$/,
  /^[a-z]{1,2}\d{1,2}$/,
];

function isSlugLike(value: string): boolean {
  const segments = value.split(/[-_./]/).filter(Boolean);
  return segments.length > 0 && segments.every((s) => WORD_SEGMENT_RES.some((re) => re.test(s)));
}

function shannonEntropy(value: string): number {
  const counts = new Map<string, number>();
  for (const char of value) counts.set(char, (counts.get(char) ?? 0) + 1);
  let bits = 0;
  for (const count of counts.values()) {
    const p = count / value.length;
    bits -= p * Math.log2(p);
  }
  return bits;
}

/**
 * True when a header value looks like a credential: an auth-scheme token
 * (Bearer, Basic, ...), a well-known key prefix (sk-, ghp_, xox*-, AIza, ...),
 * a JWT, or a long unbroken string of mixed letters and digits with high
 * entropy. A UUID, a URL, a date and text with spaces are not.
 */
export function looksLikeCredentialValue(value: unknown): boolean {
  if (typeof value !== "string") return false;
  const v = value.trim();
  if (v.length < 8) return false;
  if (CREDENTIAL_SCHEME_RE.test(v) || CREDENTIAL_PREFIX_RE.test(v) || JWT_RE.test(v)) return true;
  if (v.length < MIN_ENTROPY_LENGTH || UUID_RE.test(v) || !TOKEN_CHARS_RE.test(v)) return false;
  if (!/\d/.test(v) || !/[A-Za-z]/.test(v) || isSlugLike(v)) return false;
  return shannonEntropy(v) >= MIN_ENTROPY_BITS;
}

/** Placeholder a response shows in place of a stored header value. */
export const HEADER_VALUE_PLACEHOLDER = "__stored__";

/**
 * Copy of a header map with every value replaced by HEADER_VALUE_PLACEHOLDER, for
 * responses. The names stay so a form can list them; the values never leave the
 * database.
 */
export function redactHeaderValues(headers: unknown): Record<string, string> | null {
  if (!headers || typeof headers !== "object" || Array.isArray(headers)) return null;
  const out: Record<string, string> = {};
  for (const name of Object.keys(headers as Record<string, unknown>)) {
    out[name] = HEADER_VALUE_PLACEHOLDER;
  }
  return out;
}

/**
 * Header map to store when a form sends back the placeholder for a value it did
 * not retype: the stored value is kept for those names, and dropped when no
 * stored value exists.
 */
export function mergeStoredHeaderValues(
  incoming: Record<string, string> | null | undefined,
  stored: unknown
): Record<string, string> | null {
  if (!incoming) return null;
  const before =
    stored && typeof stored === "object" && !Array.isArray(stored)
      ? (stored as Record<string, unknown>)
      : {};
  const out: Record<string, string> = {};
  for (const [name, value] of Object.entries(incoming)) {
    if (value !== HEADER_VALUE_PLACEHOLDER) {
      out[name] = value;
    } else if (typeof before[name] === "string") {
      out[name] = before[name] as string;
    }
  }
  return Object.keys(out).length > 0 ? out : null;
}
