/**
 * links.ts — pure config for the two Radar "get a supporter key" outbound
 * links (F4/T7): the contributor-claim flow and the supporter-plans page of
 * the operator's own feed server. Both are unset by default.
 *
 * DELIBERATELY DB-FREE and side-effect-free, and driven by env vars only.
 *
 * These functions are read server-side only (inside a route handler) and the
 * resolved URLs are relayed to the client via GET /api/radar/settings — the
 * dashboard page never reads `process.env` itself, matching the pattern the
 * D28 referral links already established for the private feed.
 *
 * No price or monetary value is ever resolved, stored, or exposed here — the
 * URLs point at pages that are themselves the ONLY place pricing lives
 * (spec D14: no pricing in the OSS repo).
 */

import { parseRadarAdminUrl } from "@/shared/validation/radarAdminUrl";

/**
 * URL that starts the "I'm a contributor" claim flow on the operator's own
 * feed server. There is no default: without `RADAR_CONTRIBUTOR_CLAIM_URL` this
 * is null and the dashboard shows no button.
 */
export function getContributorClaimUrl(): string | null {
  return process.env.RADAR_CONTRIBUTOR_CLAIM_URL?.trim() || null;
}

/**
 * URL of the operator's own supporter plans page. No default: without
 * `RADAR_SUPPORTER_PLANS_URL` this is null and the dashboard shows no button.
 */
export function getSupporterPlansUrl(): string | null {
  return process.env.RADAR_SUPPORTER_PLANS_URL?.trim() || null;
}

/**
 * Private operations-panel URL configured by the instance owner.
 * Unlike the public supporter flows, this deliberately has no default.
 */
export function getRadarAdminUrl(): string | null {
  return parseRadarAdminUrl(process.env.RADAR_ADMIN_URL);
}
