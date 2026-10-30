/**
 * FreeRoute brand mark — a rounded-lg square, bg-primary/text-primary-foreground,
 * bold 2-letter "FR" monogram. Matches OpenVault's brand mark convention
 * (OpenVault itself uses "OV"); see PRODUCT_ROLES.md.
 */
type OmniRouteLogoProps = {
  size?: number;
  className?: string;
};

export default function OmniRouteLogo({ size = 20, className = "" }: OmniRouteLogoProps) {
  return (
    <span
      role="img"
      aria-label="FreeRoute"
      suppressHydrationWarning
      className={`inline-flex shrink-0 items-center justify-center rounded-lg bg-primary font-bold leading-none text-primary-foreground ${className}`}
      style={{ width: size, height: size, fontSize: Math.max(9, Math.round(size * 0.34)) }}
    >
      FR
    </span>
  );
}
