interface PageHeroProps {
  title: string;
  description?: string;
  /** Optional eyebrow above the title (e.g. "Sentinel"). */
  eyebrow?: string;
}

/**
 * Large centred hero for section landing pages.
 *
 * The vendor original hardcoded `text-white` because it only ever sat on their
 * dark marketing background. Ours renders on all five themes, so it reads from
 * the token layer instead.
 */
export default function PageHero({ title, description, eyebrow }: PageHeroProps) {
  return (
    <div className="text-center mb-16 lg:mb-24">
      {eyebrow && (
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground mb-3">
          {eyebrow}
        </p>
      )}

      <h1 className="text-4xl md:text-5xl lg:text-6xl font-bold text-foreground mb-6">
        {title}
      </h1>

      {description && (
        <p className="text-lg lg:text-xl text-muted-foreground max-w-3xl mx-auto leading-relaxed">
          {description}
        </p>
      )}
    </div>
  );
}
