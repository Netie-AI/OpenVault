"use client";

import Link from "next/link";
import { useI18n } from "@/components/i18n-provider";

interface MigratedLauncherProps {
  /** Where the data now lives. Operator goes here for the real dashboard. */
  migrationTargetUrl: string;
  /**
   * Migration variant — controls copy.
   *   self_hosted_remote → operator's own VPS (the only path this fork's UI
   *     can still start — see MigrateModal.tsx)
   *
   * Modified by Netie AI, 2026: "cloud_hosted" and "tunneled" removed from
   * the type. FreeBuild is self-hosted only; nothing can put an instance in
   * either state anymore (start-cloud/start-tunnel always answer 501
   * hosted_cloud_disabled). A `teamMode` column value from before this fork
   * disabled them would fall through the switch below same as any other
   * non-"self_hosted_remote" value — the server variant, which is at least
   * accurate about there being no cloud/tunnel destination to launch into.
   */
  teamMode: "self_hosted_remote" | "cloud_hosted" | "tunneled";
}

/**
 * Shown in place of the dashboard when this instance has been migrated
 * to a multi-user deployment. The DB / API / runtime all live at
 * `migrationTargetUrl` now; this local instance is a stale shell that
 * exists only as a launcher (and a "switch back" escape hatch).
 *
 * Stays intentionally minimal — every piece of dynamic data that used
 * to power the dashboard is on the remote side, not here.
 */
export function MigratedLauncher({
  migrationTargetUrl,
  teamMode,
}: MigratedLauncherProps) {
  const { t } = useI18n();
  const m = t.chrome.migration;
  // Every teamMode this fork can produce renders the same "moved to your
  // server" copy — see the type doc above.
  const variant = { title: m.serverTitle, body: m.serverBody, cta: m.serverCta };
  void teamMode;

  return (
    <div className="flex min-h-dvh flex-col items-center justify-center bg-black p-8 text-white">
      <div className="w-full max-w-lg space-y-6 rounded-2xl border border-white/10 bg-white/[0.02] p-8">
        <div className="space-y-2">
          <h1 className="text-2xl font-semibold">{variant.title}</h1>
          <p className="text-sm text-white/60">{variant.body}</p>
        </div>

        <div className="space-y-2 rounded-lg border border-white/10 bg-white/[0.03] px-4 py-3">
          <p className="text-xs uppercase tracking-wider text-white/40">
            {m.newLocation}
          </p>
          <p className="break-all font-mono text-sm text-white">
            {migrationTargetUrl}
          </p>
        </div>

        <Link
          href={migrationTargetUrl}
          className="inline-flex w-full items-center justify-center rounded-lg bg-white px-4 py-3 text-sm font-medium text-black transition hover:bg-white/90"
        >
          {variant.cta}
        </Link>

        <div className="space-y-1 border-t border-white/10 pt-6">
          <p className="text-xs text-white/40">
            {m.switchBackPrompt}
          </p>
          <Link
            href="/settings/migration/switch-back"
            className="text-sm text-white/70 underline-offset-4 hover:underline"
          >
            {m.switchBackAction}
          </Link>
        </div>
      </div>
    </div>
  );
}
