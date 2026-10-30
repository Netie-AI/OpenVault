// FreeRoute: keyless Pollinations image requests.
//
// Upstream (#8085) acquired a session from a rotating browser-fingerprint pool
// (rotating user agents and Sec-CH-UA client hints) and merged those headers into
// every keyless image request so the provider could not tie requests to one
// client. That is browser fingerprint impersonation, which is not shipped
// (tls_fingerprint_stealth_disabled), and it bypassed the BaseExecutor.getPool()
// gate that already keeps the chat path honest.
//
// This edition sends the request with the plain headers the caller built (JSON
// content type, plus the provider key when there is one) and nothing else. A
// keyless request that the provider rejects surfaces the provider's own 401 or
// 429; configure a Pollinations key in OpenVault to avoid it.
//
// The two exports keep the call sites in open-sse/handlers/imageGeneration.ts
// unchanged. They no longer take, report or release any session.

/** The session slot is always null: no fingerprint pool exists in this edition. */
export type PollinationsAnonSession = null;

/**
 * Returns `headers` untouched and no session, for every provider and for
 * keyless Pollinations alike. No rotating fingerprint headers are added.
 */
export async function applyPollinationsAnonymousFallback(
  _providerId: string,
  _token: string | undefined,
  headers: Record<string, string>
): Promise<{ headers: Record<string, string>; session: PollinationsAnonSession }> {
  return { headers, session: null };
}

/** No-op: there is no pool to report an outcome to. */
export function reportPollinationsAnonOutcome(
  _session: PollinationsAnonSession,
  _status: number | undefined
): void {}
