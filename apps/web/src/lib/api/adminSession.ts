/**
 * Admin session shared with the Providers page.
 *
 * The value lives in sessionStorage under `openvault.admin` and is sent only
 * as `X-OpenVault-Admin`. It is never logged and never written into a URL.
 */

export const ADMIN_SESSION_KEY = "openvault.admin";

export const ADMIN_HEADER = "X-OpenVault-Admin";

const ADMIN_ROOTS: readonly string[] = [
  "/api/keys",
  "/api/keyvault",
  "/api/apikeys",
  "/api/secrets",
  "/api/vault",
  "/api/ship/github/pat",
  "/keys",
];

const ACCOUNT_KEY = /^\/api\/accounts\/[^/]+\/(?:keys|cortex-key)(?:\/|$)/;

/** Strip a query string. A full URL is not an admin path. */
export function pathNeedsAdmin(path: string): boolean {
  const bare = path.split("?")[0] ?? path;
  if (bare === "/keys/jwks" || bare === "/.well-known/jwks.json") return false;
  for (const root of ADMIN_ROOTS) {
    if (bare === root || bare.startsWith(`${root}/`)) return true;
  }
  return ACCOUNT_KEY.test(bare);
}

/**
 * Attach the session token on admin routes when the caller did not already
 * set the header. An empty token is omitted so we do not send a blank credential.
 */
export function mergeAdminHeader(
  path: string,
  headers: Record<string, string>,
  token: string,
): Record<string, string> {
  if (!pathNeedsAdmin(path)) return headers;
  const presented = token.trim();
  if (!presented) return headers;
  if (headers[ADMIN_HEADER] || headers[ADMIN_HEADER.toLowerCase()]) return headers;
  return { ...headers, [ADMIN_HEADER]: presented };
}

export function readAdminSession(): string {
  if (typeof sessionStorage === "undefined") return "";
  try {
    return sessionStorage.getItem(ADMIN_SESSION_KEY) ?? "";
  } catch {
    return "";
  }
}

export function writeAdminSession(value: string): void {
  if (typeof sessionStorage === "undefined") return;
  try {
    sessionStorage.setItem(ADMIN_SESSION_KEY, value);
  } catch {
    /* session storage unavailable; the field still holds it in memory */
  }
}

/** Remove credential material from a string that might be shown in the UI. */
export function redactShown(text: string, hidden: readonly string[]): string {
  let out = text;
  for (const item of hidden) {
    const token = item.trim();
    if (token.length < 4) continue;
    if (!out.includes(token)) continue;
    out = out.split(token).join("");
  }
  return out;
}
