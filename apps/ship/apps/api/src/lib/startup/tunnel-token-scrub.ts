// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
/**
 * Clear a tunnel token an earlier build stored in `instance_settings.tunnel_token`.
 *
 * FreeBuild no longer stores one (settings.operations.ts: assertNoTunnelToken) and
 * nothing reads the column, so a leftover value is a secret on disk for no purpose.
 * Idempotent and best-effort like the other boot reconciles. The database write-ahead
 * log can still hold the old bytes until PGlite checkpoints.
 */

import { repos } from "@repo/db";
import { safeErrorMessage } from "@repo/core";
import { registerStartupHook } from "@repo/platform/engine/lib/startup/index";

export async function scrubStoredTunnelToken(): Promise<boolean> {
  try {
    const row = await repos.instanceSettings.get();
    if (!row?.tunnelToken) return false;
    await repos.instanceSettings.upsert({ tunnelToken: null });
    console.warn(
      "[tunnel-token-scrub] removed a tunnel token stored by an earlier build. " +
        "FreeBuild does not keep tunnel tokens; Cloudflare secrets belong in OpenVault's KeyVault.",
    );
    return true;
  } catch (err) {
    console.warn(`[tunnel-token-scrub] skipped: ${safeErrorMessage(err)}`);
    return false;
  }
}

export function registerTunnelTokenScrub(): void {
  registerStartupHook({
    id: "settings:tunnel-token-scrub",
    modes: ["selfhosted", "desktop"],
    run: async () => {
      await scrubStoredTunnelToken();
    },
  });
}
