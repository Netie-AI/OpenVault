"use client";

/**
 * FreeRoute: no upstream changelog fetch.
 *
 * Upstream OmniRoute fetched CHANGELOG.md from
 * raw.githubusercontent.com/diegosouzapw/OmniRoute and rendered it here.
 * FreeRoute must not display upstream OmniRoute release history — its own
 * changes are tracked in OpenVault's CHANGELOG.md instead.
 */
export default function ChangelogViewer() {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-32 text-center">
      <span className="material-symbols-outlined text-[40px] text-text-muted/50" aria-hidden="true">
        history_edu
      </span>
      <p className="max-w-md text-sm text-text-muted">
        FreeRoute&apos;s changelog lives in OpenVault&apos;s <code>CHANGELOG.md</code>, not here.
      </p>
    </div>
  );
}
