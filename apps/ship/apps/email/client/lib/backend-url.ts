/**
 * Single source of truth for the Zero server URL.
 *
 * Zero is always served same-origin - the Hono server hosts BOTH the SPA
 * (under `/`) AND the API (under `/api/*`, `/auth/*`, …) on the same
 * port. So at runtime the backend URL is just whatever the browser
 * loaded the page from - `window.location.origin`. No env, no build-time
 * baking, one build deploys anywhere.
 *
 * Dev still needs a fallback: when the Vite dev server runs the SPA on
 * port 3000 and the Hono server runs on 3030, `window.location.origin`
 * points at the wrong place. The `VITE_PUBLIC_BACKEND_URL` env var,
 * read here purely as an OPTIONAL dev override, fills that gap. In
 * production builds it's intentionally unused - Vite still inlines it
 * if set, but the runtime preference wins.
 *
 * SSR / pre-render: `window` doesn't exist. SPA mode means the static
 * HTML shell isn't actually issuing tRPC requests during SSR, so any
 * placeholder works. We return `''` and let the hydrated client
 * recompute against the real origin.
 */

function isBrowser(): boolean {
  return typeof window !== 'undefined' && !!window.location?.origin;
}

export function getBackendUrl(): string {
  if (isBrowser()) return window.location.origin;
  // Dev: Vite SPA on 3000 → Hono on 3030 - same-origin doesn't apply.
  // Only consulted off-browser (SSR / module-load on Node) where the
  // import.meta.env access is safe.
  const fromEnv =
    typeof import.meta !== 'undefined'
      ? (import.meta.env?.VITE_PUBLIC_BACKEND_URL as string | undefined)
      : undefined;
  if (fromEnv && fromEnv !== 'undefined') return fromEnv;
  return '';
}

export function getTrpcUrl(): string {
  return `${getBackendUrl()}/api/trpc`;
}

/**
 * Legacy const exports - module-load-time values. Browser builds get the
 * runtime origin; SSR/Node builds get '' (or the dev override). New code
 * should call the getter functions above so a same-page navigation can't
 * latch a stale value.
 */
export const BACKEND_URL = getBackendUrl();
export const TRPC_URL = getTrpcUrl();
