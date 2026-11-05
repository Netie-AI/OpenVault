/**
 * Cached IANA timezone list. Used by the settings UI to render the
 * timezone selector. We use `Intl.supportedValuesOf` when available
 * and fall back to a hand-picked common subset.
 */

function intlList(): string[] | null {
  try {
    const fn = (Intl as unknown as { supportedValuesOf?: (k: string) => string[] })
      .supportedValuesOf;
    if (typeof fn === 'function') return fn('timeZone');
  } catch {
    /* not available */
  }
  return null;
}

const FALLBACK = [
  'UTC',
  'America/New_York',
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'Europe/London',
  'Europe/Paris',
  'Europe/Berlin',
  'Europe/Madrid',
  'Europe/Rome',
  'Asia/Dubai',
  'Asia/Karachi',
  'Asia/Kolkata',
  'Asia/Singapore',
  'Asia/Tokyo',
  'Australia/Sydney',
];

export const timezones: string[] = intlList() ?? FALLBACK;
