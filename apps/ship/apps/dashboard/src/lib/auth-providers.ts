import type { AdvertisedAuthProvider } from "@repo/contracts";
export type { AdvertisedAuthProvider } from "@repo/contracts";

// Presentation order and supported icons/labels belong to this client build.
const RENDERABLE_OAUTH_PROVIDERS = ["github", "google"] as const;
export type RenderableOAuthProviderId = (typeof RENDERABLE_OAUTH_PROVIDERS)[number];

/** Render only supported social providers advertised by the server. */
export function renderableOAuthProviders(
  advertised: readonly AdvertisedAuthProvider[] | undefined,
): RenderableOAuthProviderId[] {
  const offered = new Set(
    (advertised ?? []).filter(provider => provider.kind === "social").map(provider => provider.id),
  );
  return RENDERABLE_OAUTH_PROVIDERS.filter(id => offered.has(id));
}
