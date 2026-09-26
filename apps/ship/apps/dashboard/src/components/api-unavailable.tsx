"use client";

import { Icon as UiIcon } from "@repo/ui/icons";

import { ErrorView } from "@/components/error-view";
import { useBrandName, useI18n } from "@/components/i18n-provider";

/**
 * Full-page fallback shown when the API can't be reached during SSR bootstrap
 * (see getDeploymentInfoOrNull). The deploy/auth mode is known only to the API,
 * so rather than guess it (and render the wrong login flow) or crash into the
 * error boundary, we render this explicit screen.
 *
 * The SEGMENT layouts return this ((dashboard)/(auth)/(onboarding)), so the ROOT
 * layout — globals.css, ThemeScript, ThemeProvider, I18nProvider — is still
 * mounted around it: theme tokens and translations both work here, which is why
 * this is a normal themed component and not the inline-styled white page it used
 * to be. Retry reloads, which re-fetches /health/env; if the API is back, the app
 * loads normally.
 */
export function ApiUnavailable() {
  const { t } = useI18n();
  const brand = useBrandName();
  const c = t.chrome;

  // Modified by Netie AI, 2026: no docsHref passed below — openship.io was
  // never this fork's site; error-view.tsx renders only the GitHub credit
  // link when docsHref is unset.
  return (
    <div className="flex min-h-dvh items-center justify-center px-6 py-12">
      <ErrorView
        variant="offline"
        brand={brand}
        title={c.apiDown.title}
        description={c.apiDown.description}
        hints={[
          <Hint key="status" text={c.apiDown.hintStatus} />,
          <Hint key="start" text={c.apiDown.hintStart} />,
          <Hint key="remote" text={c.apiDown.hintRemote} />,
        ]}
        actions={[
          {
            label: c.apiDown.retry,
            onClick: () => window.location.reload(),
            icon: <UiIcon name="refresh" className="size-4" />,
          },
        ]}
        docsLabel={c.apiDown.docs}
        githubLabel={c.errorLinks.github}
      />
    </div>
  );
}

/** Renders `\`backticked\`` spans in a hint as inline code, so the commands the
 *  operator has to run actually look runnable. */
function Hint({ text }: { text: string }) {
  const parts = text.split(/`([^`]+)`/g);
  return (
    <>
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <code
            key={i}
            className="rounded-md bg-foreground/[0.07] px-1.5 py-0.5 font-mono text-[12px] text-foreground/80"
          >
            {part}
          </code>
        ) : (
          part
        ),
      )}
    </>
  );
}
