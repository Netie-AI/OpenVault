import { hostname, platform } from "node:os";

/**
 * Resolve the bind host passed to the standalone Next.js server.
 *
 * HOSTNAME is a standard shell variable on Unix-like systems, so only the
 * dedicated FreeRoute variable is treated as configuration there. Windows
 * keeps the legacy HOSTNAME fallback for compatibility with existing .env
 * files, while still ignoring the OS-reported machine name.
 *
 * @param {NodeJS.ProcessEnv} [env]
 * @param {NodeJS.Platform} [runtimePlatform]
 * @param {string} [machineHostname]
 * @returns {string}
 */
export function resolveServerHost(
  env = process.env,
  runtimePlatform = platform(),
  machineHostname = hostname()
) {
  if (env.OMNIROUTE_SERVER_HOST) return env.OMNIROUTE_SERVER_HOST;
  if (env.HOST) return env.HOST;
  if (env.OMNIROUTE_HOSTNAME) return env.OMNIROUTE_HOSTNAME;
  if (runtimePlatform === "win32" && env.HOSTNAME && env.HOSTNAME !== machineHostname) {
    return env.HOSTNAME;
  }
  // FreeRoute binds loopback by default (DR-0018). Set HOST, OMNIROUTE_HOSTNAME
  // or OMNIROUTE_SERVER_HOST to listen on another interface.
  return "127.0.0.1";
}

const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);

/**
 * Boot-time exposure warning (GHSA-wmgv-ph3p-rv57): when an operator binds a
 * non-loopback interface while the inference plane requires no credentials,
 * any LAN peer can spend the operator's quota. The warning must be loud at
 * startup so the operator learns the two escape hatches.
 *
 * Returns the warning text when the server will listen on a non-loopback
 * interface with no API-key requirement, or null when the exposure is closed.
 *
 * @param {NodeJS.ProcessEnv} [env]
 * @param {string} [host]
 * @returns {string | null}
 */
export function resolveExposureWarning(env = process.env, host = resolveServerHost(env)) {
  if (LOOPBACK_HOSTS.has(host)) return null;
  const requireKey = String(env.REQUIRE_API_KEY || "")
    .trim()
    .toLowerCase();
  if (requireKey === "true" || requireKey === "1" || requireKey === "yes") return null;
  return (
    `SECURITY: listening on ${host} with NO API-key requirement. The inference ` +
    `plane (/v1/*) is reachable by ANY device that can route to this host, and ` +
    `requests are billed to your configured providers. Either set ` +
    `REQUIRE_API_KEY=true or bind loopback by unsetting HOST, OMNIROUTE_HOSTNAME ` +
    `and OMNIROUTE_SERVER_HOST.`
  );
}
