/**
 * Shared page header. The vendor original resolved its strings through an i18n
 * provider; we ship English only, so the caller passes them directly.
 */
export function PageHeader({
  title,
  description,
}: {
  title: string;
  description?: string;
}) {
  return (
    <div className="mb-8">
      <h1 className="text-xl font-semibold tracking-tight text-foreground">{title}</h1>
      {description && <p className="mt-1.5 text-sm text-muted-foreground">{description}</p>}
    </div>
  );
}

export default PageHeader;
