/**
 * ngrok tunnel provider — STUB.
 *
 * Planned shape:
 *   - Credentials: NGROK_AUTHTOKEN env var (cloud-mode) OR per-user
 *     encrypted setting (self-hosted). Provider context can carry
 *     a token override per-call.
 *   - Slug behavior: free tier emits random URLs and ignores slug.
 *     Paid tier honors `subdomain` (slug-equivalent) and `hostname`.
 *   - Agent: official @ngrok/ngrok SDK opens an inline tunnel,
 *     emits "disconnect"/"error"/"close" events matching the
 *     TunnelAgent interface — just wrap.
 *
 * Not implemented yet. Calls throw ProviderNotImplementedError so
 * the registry can list the provider as known-but-unavailable
 * (useful for UI dropdowns that need to dim a coming-soon row).
 */

import type { TunnelProvider } from "../types";
import { ProviderNotImplementedError } from "../types";

export const ngrokProvider: TunnelProvider = {
  name: "ngrok",

  async preflight() {
    return {
      ok: false,
      reason: "ngrok provider is not implemented yet.",
    };
  },

  async create() {
    throw new ProviderNotImplementedError("ngrok");
  },

  async delete() {
    throw new ProviderNotImplementedError("ngrok");
  },

  async connect() {
    throw new ProviderNotImplementedError("ngrok");
  },
};
