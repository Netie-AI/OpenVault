import type { AdvertisedAuthProvider } from "@repo/contracts";
import { env } from "../config/env";

const SOCIAL_AUTH_PROVIDERS = ["github", "google"] as const;
type SocialAuthProviderId = (typeof SOCIAL_AUTH_PROVIDERS)[number];

/** Shared credential predicate for Better Auth registration and public discovery. */
export function socialProviderCredentials(
  id: SocialAuthProviderId,
): { clientId: string; clientSecret: string } | null {
  const pair = id === "github"
    ? { clientId: env.GITHUB_CLIENT_ID, clientSecret: env.GITHUB_CLIENT_SECRET }
    : { clientId: env.GOOGLE_CLIENT_ID, clientSecret: env.GOOGLE_CLIENT_SECRET };
  if (!pair.clientId || !pair.clientSecret) return null;
  return { clientId: pair.clientId, clientSecret: pair.clientSecret };
}

/** Public response: IDs and kinds only; never return the registration credentials. */
export function configuredAuthProviders(): AdvertisedAuthProvider[] {
  return SOCIAL_AUTH_PROVIDERS.filter(id => socialProviderCredentials(id) !== null)
    .map(id => ({ id, kind: "social" }));
}
