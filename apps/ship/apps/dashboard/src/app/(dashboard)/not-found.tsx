import { NotFoundContent } from "@/components/not-found-content";

/**
 * 404 for authenticated in-dashboard misses (e.g. a bad `notFound()` route).
 * Renders inside (dashboard)/layout.tsx's <main>, so it keeps the real sidebar
 * chrome — the wrapper fills that main area and dead-centers the 404 body.
 * Arbitrary unmatched URLs still hit the global app/not-found.tsx.
 */
export default function DashboardNotFound() {
  return (
    <div className="flex min-h-full items-center justify-center px-6 py-10">
      <NotFoundContent variant="dashboard" />
    </div>
  );
}
