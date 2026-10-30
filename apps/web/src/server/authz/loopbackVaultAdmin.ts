/**
 * Loopback console attach for vault bootstrap (#123).
 *
 * FastAPI still requires X-OpenVault-Admin on /api/vault and /api/keys,
 * including from 127.0.0.1. The browser must not paste that token. When the
 * request is addressed to a loopback host, this reads the local admin file and
 * sets the header on the upstream request for status, passphrase unseal, Hello
 * unseal, and GET /api/keys. Next applies that header to the /ov-api rewrite
 * and strips it from the browser response.
 *
 * "Addressed to a loopback host" means BOTH of these hold:
 *   1. the middleware URL host is loopback, and
 *   2. the raw request `Host` header is loopback (127.0.0.1, localhost, ::1).
 *
 * (1) alone is not a gate. `next start -H 127.0.0.1` builds the middleware
 * URL from the server's bind hostname, not from the request, so it is loopback
 * for every request. A DNS-rebinding page sends `Host: evil.example:3010` to
 * 127.0.0.1:3010 and only the Host header differs. (2) is what refuses it.
 *
 * The file is not read for any other method or path, and never when the Host
 * header is missing, empty, repeated, or not loopback. A trusted
 * X-Forwarded-For does not count. Other vault and key mutations stay without
 * this header, so they remain 401 unless the caller already presented one.
 */

import { readFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import { isLoopbackHost } from "./routeGuard";

export const LOOPBACK_ADMIN_HEADER = "X-OpenVault-Admin";

const BOOTSTRAP: ReadonlyArray<{ method: string; path: string }> = [
  { method: "GET", path: "/api/vault/status" },
  { method: "POST", path: "/api/vault/unseal" },
  { method: "POST", path: "/api/vault/webauthn/unseal/begin" },
  { method: "POST", path: "/api/vault/webauthn/unseal/finish" },
  { method: "GET", path: "/api/keys" },
];

/** Path after the /ov-api prefix, with no query and no traversal. */
export function vaultBackendPath(pathname: string): string {
  let raw = (pathname.split("?")[0] ?? pathname).trim();
  if (!raw || raw.includes("\\") || raw.includes("..")) return "";
  if (raw.length > 1 && raw.endsWith("/")) raw = raw.slice(0, -1);
  if (raw === "/ov-api") return "/";
  if (raw.startsWith("/ov-api/")) return raw.slice("/ov-api".length) || "/";
  return raw.startsWith("/") ? raw : "";
}

const LOOPBACK_HOST_NAMES: ReadonlySet<string> = new Set(["127.0.0.1", "localhost", "::1"]);

/**
 * True only when a raw `Host` header value names a loopback host. Fails closed.
 *
 * Accepts `127.0.0.1`, `localhost`, `[::1]` and a bare `::1`, each with an
 * optional `:port` (1-5 digits) after the host, case-insensitive. Everything
 * else is false: missing or empty, whitespace or control characters, a comma
 * (a repeated Host header joins with one), a dotted-suffix name such as
 * `127.0.0.1.evil.example`, and a trailing-dot FQDN such as `localhost.`. The
 * trailing dot is refused on purpose: it is a distinct DNS name and no
 * console client sends it.
 *
 * This does not reuse `isLoopbackHost`: that helper trims, strips a mapped
 * `::ffff:` prefix and accepts unbracketed multi-colon text, which is wider than
 * a gate on an attacker-chosen header should be.
 */
export function hostHeaderIsLoopback(hostHeader: string | null | undefined): boolean {
  if (!hostHeader) return false;
  // Whitespace, control characters and commas (a repeated header joins with one).
  for (const ch of hostHeader) {
    const code = ch.charCodeAt(0);
    if (code <= 0x20 || code === 0x7f || ch === ",") return false;
  }

  let name: string;
  let port = "";
  if (hostHeader.startsWith("[")) {
    const end = hostHeader.indexOf("]");
    if (end < 0) return false;
    name = hostHeader.slice(1, end);
    const rest = hostHeader.slice(end + 1);
    if (rest) {
      if (!rest.startsWith(":")) return false;
      port = rest.slice(1);
      if (!/^\d{1,5}$/.test(port)) return false;
    }
  } else {
    const colons = hostHeader.split(":").length - 1;
    if (colons === 0) {
      name = hostHeader;
    } else if (colons === 1) {
      const at = hostHeader.indexOf(":");
      name = hostHeader.slice(0, at);
      port = hostHeader.slice(at + 1);
      if (!/^\d{1,5}$/.test(port)) return false;
    } else {
      // Only the bare IPv6 loopback has several colons and no brackets.
      name = hostHeader;
    }
  }
  return LOOPBACK_HOST_NAMES.has(name.toLowerCase());
}

export function isLoopbackVaultBootstrap(method: string, pathname: string): boolean {
  const verb = method.toUpperCase();
  const bare = vaultBackendPath(pathname);
  if (!bare) return false;
  return BOOTSTRAP.some((row) => row.method === verb && row.path === bare);
}

function expandHome(value: string, homedir: string): string {
  const trimmed = value.trim();
  if (trimmed === "~") return homedir;
  if (trimmed.startsWith("~/") || trimmed.startsWith("~\\")) {
    return path.join(homedir, trimmed.slice(2));
  }
  return trimmed;
}

/** Same file FastAPI uses: OPENVAULT_ADMIN_TOKEN_PATH, else $OPENVAULT_HOME/admin_token. */
export function localAdminTokenPath(
  env: NodeJS.ProcessEnv = process.env,
  homedir: string = os.homedir(),
): string {
  const override = (env.OPENVAULT_ADMIN_TOKEN_PATH ?? "").trim();
  if (override) return expandHome(override, homedir);
  const home = (env.OPENVAULT_HOME ?? "").trim();
  const root = home ? expandHome(home, homedir) : path.join(homedir, ".openvault");
  return path.join(root, "admin_token");
}

/** Reject blank, huge, or header-breaking values. Never logs the input. */
export function sanitizeAdminToken(raw: string): string {
  const token = raw.trim();
  if (!token || token.length > 512) return "";
  if (/[\r\n\0,]/.test(token)) return "";
  return token;
}

export function readLocalAdminToken(
  env: NodeJS.ProcessEnv = process.env,
  read: (file: string) => string = (file) => readFileSync(file, "utf8"),
): string {
  try {
    return sanitizeAdminToken(read(localAdminTokenPath(env)));
  } catch {
    return "";
  }
}

/**
 * Set the upstream admin header. Returns false when the request must stay
 * unauthenticated at this layer. `urlHost` is the middleware URL host and
 * `hostHeader` is the raw request Host header; both must be loopback.
 */
export function applyLoopbackAdminHeader(
  headers: Headers,
  input: {
    method: string;
    pathname: string;
    urlHost: string;
    hostHeader: string | null;
    token: string;
  },
): boolean {
  if (!isLoopbackHost(input.urlHost)) return false;
  if (!hostHeaderIsLoopback(input.hostHeader)) return false;
  if (!isLoopbackVaultBootstrap(input.method, input.pathname)) return false;
  const token = sanitizeAdminToken(input.token);
  if (!token) return false;
  headers.set(LOOPBACK_ADMIN_HEADER, token);
  return true;
}
