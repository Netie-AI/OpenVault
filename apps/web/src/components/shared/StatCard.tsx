import type { LucideIcon } from "lucide-react";

interface StatCardProps {
  /** The headline figure, already formatted ("18", "4.2 TB", "97%"). */
  number: string;
  label: string;
  icon?: LucideIcon;
  /** Semantic tone for the figure — use for health/threshold readouts. */
  tone?: "default" | "success" | "warning" | "danger";
}

const TONE_TEXT: Record<NonNullable<StatCardProps["tone"]>, string> = {
  default: "text-foreground",
  success: "text-success",
  warning: "text-warning",
  danger: "text-danger",
};

/**
 * Single-figure tile for overview grids.
 *
 * Retokened from the vendor original, which was `bg-white/5` + `text-white` —
 * invisible on a light theme.
 */
export default function StatCard({ number, label, icon: Icon, tone = "default" }: StatCardProps) {
  return (
    <div className="rounded-2xl border border-border bg-card p-6 text-center">
      {Icon && (
        <Icon className="size-5 mx-auto mb-3 text-muted-foreground" aria-hidden="true" />
      )}
      <div className={`text-3xl lg:text-4xl font-bold mb-2 tabular-nums ${TONE_TEXT[tone]}`}>
        {number}
      </div>
      <div className="text-sm lg:text-base text-muted-foreground">{label}</div>
    </div>
  );
}
