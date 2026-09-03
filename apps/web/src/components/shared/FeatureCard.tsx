import type { LucideIcon } from "lucide-react";

interface FeatureCardProps {
  /** Lucide component, not a name string — see ValueCard for why. */
  icon?: LucideIcon;
  title: string;
  description: string;
}

/** Smaller sibling of ValueCard, for feature grids. */
export default function FeatureCard({ icon: Icon, title, description }: FeatureCardProps) {
  return (
    <div className="rounded-2xl border border-border bg-card p-6">
      {Icon && (
        <div className="mb-4">
          <Icon className="size-10 text-muted-foreground" aria-hidden="true" />
        </div>
      )}
      <h3 className="text-xl font-bold text-foreground mb-3">{title}</h3>
      <p className="text-muted-foreground leading-relaxed">{description}</p>
    </div>
  );
}
