/**
 * Cloudflare tunnel provider — STUB.
 *
 * Planned shape (two flavors):
 *   1. Quick tunnel (no account): spawn the `cloudflared tunnel`
 *      binary in --url mode; parse the trycloudflare.com URL from
 *      stderr; agent process is the actual cloudflared subprocess
 *      (wrap as TunnelAgent via Node child_process).
 *   2. Named tunnel (user's account): requires a CF API token in
 *      provider context (or per-instance setting). Creates a Tunnel
 *      resource, generates credentials, runs `cloudflared tunnel
 *      run --token <jwt>`.
 *
 * Slug behavior:
 *   - Quick tunnel ignores slug — returns a random *.trycloudflare.com.
 *   - Named tunnel honors hostname (set via DNS record + ingress rule).
 *
 * Not implemented yet. Calls throw ProviderNotImplementedError so the
 * registry can list the provider as known-but-unavailable.
 */

import type { TunnelProvider } from "../types";
import { ProviderNotImplementedError } from "../types";

export const cloudflareProvider: TunnelProvider = {
  name: "cloudflare",

  async preflight() {
    return {
      ok: false,
      reason: "Cloudflare provider is not implemented yet.",
    };
  },

  async create() {
    throw new ProviderNotImplementedError("cloudflare");
  },

  async delete() {
    throw new ProviderNotImplementedError("cloudflare");
  },

  async connect() {
    throw new ProviderNotImplementedError("cloudflare");
  },
};
