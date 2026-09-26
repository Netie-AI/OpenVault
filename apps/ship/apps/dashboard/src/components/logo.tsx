/**
 * FreeBuild logo — the Netie "FB" monogram: a rounded-lg square in
 * `bg-primary`/`text-primary-foreground`, matching OpenVault's own brand mark
 * (see PRODUCT_ROLES.md / the Design section of the fork brief). Plain CSS +
 * text, no image asset, so it renders instantly with no JS theme check.
 * Modified by Netie AI, 2026 — replaces Openship's bordered-circle "O" mark.
 */
export function Logo({ size = 36, className }: { size?: number; className?: string }) {
  return (
    <div
      className={`flex shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground ${className ?? ""}`}
      style={{ width: size, height: size, fontSize: 11, fontWeight: 700 }}
      aria-hidden="true"
    >
      FB
    </div>
  );
}
