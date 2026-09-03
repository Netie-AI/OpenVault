import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface PageContainerProps {
  children: ReactNode;
  className?: string;
  outerClassName?: string;
  fullScreen?: boolean;
}

/**
 * The one place in the app that owns page-level top padding.
 *
 * The app bar is fixed and full-width, so every page has to be pushed down by
 * exactly its height. That height lives in `--ov-topbar-h` (defined with the
 * theme tokens) and is read here — nowhere else. If a page sets its own `pt-*`
 * it will double- or under-pad against the bar; that is the bug this component
 * exists to prevent, so treat any `pt-*` on a page as a defect.
 */
export function PageContainer({
  children,
  className,
  outerClassName,
  fullScreen = true,
}: PageContainerProps) {
  const inner = (
    <div
      className={cn(
        "max-w-[1600px] mx-auto px-6 lg:px-8 pt-[var(--ov-topbar-h)] pb-10",
        className,
      )}
    >
      {children}
    </div>
  );

  if (!fullScreen) return inner;

  return <div className={cn("min-h-screen bg-background", outerClassName)}>{inner}</div>;
}

export default PageContainer;
