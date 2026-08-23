import type { LucideIcon } from "lucide-react";

interface ValueCardProps {
  /**
   * Lucide component, not a name string. The vendor original took a string and
   * resolved it through an icon generator that fetched SVGs from their CDN —
   * blocked by the desktop CSP, so the icon is passed in directly.
   */
  icon?: LucideIcon;
  title: string;
  description: string;
}

/** Large explanatory card — one per pillar on a section landing page. */
export default function ValueCard({ icon: Icon, title, description }: ValueCardProps) {
  return (
    <div className="rounded-2xl border border-border bg-card p-8">
      {Icon && (
        <div className="mb-4">
          <Icon className="size-12 text-muted-foreground" aria-hidden="true" />
        </div>
      )}
      <h3 className="text-2xl font-bold text-foreground mb-4">{title}</h3>
      <p className="text-muted-foreground leading-relaxed">{description}</p>
    </div>
  );
}
