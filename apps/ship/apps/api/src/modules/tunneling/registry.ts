/**
 * Provider registry — single source of truth for which TunnelProviders
 * the app knows about. Adding a provider is one line here plus the
 * provider implementation file.
 */

import type { TunnelProvider, TunnelProviderName } from "./types";
import { UnknownProviderError } from "./types";
import { oblienProvider } from "./providers/oblien.provider";
import { ngrokProvider } from "./providers/ngrok.provider";
import { cloudflareProvider } from "./providers/cloudflare.provider";

const PROVIDERS: Record<TunnelProviderName, TunnelProvider> = {
  oblien: oblienProvider,
  ngrok: ngrokProvider,
  cloudflare: cloudflareProvider,
};

export function resolveProvider(name: TunnelProviderName): TunnelProvider {
  const p = PROVIDERS[name];
  if (!p) throw new UnknownProviderError(name);
  return p;
}

export function listProviders(): TunnelProviderName[] {
  return Object.keys(PROVIDERS) as TunnelProviderName[];
}

/**
 * Convenience: list providers along with their current readiness state.
 * Useful for UI surfaces that need to dim coming-soon options.
 */
export async function describeProviders(): Promise<
  Array<{ name: TunnelProviderName; ready: boolean; reason?: string }>
> {
  return Promise.all(
    listProviders().map(async (name) => {
      const result = await PROVIDERS[name].preflight();
      return result.ok
        ? { name, ready: true }
        : { name, ready: false, reason: result.reason };
    }),
  );
}
