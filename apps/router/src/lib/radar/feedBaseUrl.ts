// Copyright (c) 2026 Netie AI. MIT.
//
// Radar has no built-in feed server. The upstream project's hosted feed is not
// part of this edition, so a feed is only ever fetched from a URL the operator
// sets in RADAR_FEED_URL. Without it every sync answers
// `upstream_service_not_included` and nothing leaves the machine.

export const RADAR_UPSTREAM_NOT_INCLUDED = "upstream_service_not_included";

/** The operator's own feed base URL (no trailing slash), or null when none is configured. */
export function getRadarFeedBaseUrl(env: NodeJS.ProcessEnv = process.env): string | null {
  const raw = env.RADAR_FEED_URL?.trim();
  if (!raw) return null;
  return raw.replace(/\/+$/, "");
}
