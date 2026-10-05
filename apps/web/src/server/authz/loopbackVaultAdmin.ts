/**
 * Loopback console attach for vault bootstrap (#123).
 *
 * FastAPI still requires X-OpenVault-Admin on /api/vault and /api/keys,
 * including from 127.0.0.1. The browser must not paste that token. When the
 * request URL host is loopback, this reads the local admin file and sets the
 * header on the upstream request for status, passphrase unseal, Hello unseal,
 * and GET /api/keys. Next applies that header to the /ov-api rewrite and
 * strips it from the browser response.
 *
 * The file is not read for any other method or path, and never for a
 * non-loopback URL host. A trusted X-Forwarded-For does not count: only the
 * URL host does. Other vault and key mutations stay without this header, so
 * they remain 401 unless the caller already presented one.
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
 * unauthenticated at this layer.
 */
export function applyLoopbackAdminHeader(
  headers: Headers,
  input: {
    method: string;
    pathname: string;
    urlHost: string;
    token: string;
  },
): boolean {
  if (!isLoopbackHost(input.urlHost)) return false;
  if (!isLoopbackVaultBootstrap(input.method, input.pathname)) return false;
  const token = sanitizeAdminToken(input.token);
  if (!token) return false;
  headers.set(LOOPBACK_ADMIN_HEADER, token);
  return true;
}
