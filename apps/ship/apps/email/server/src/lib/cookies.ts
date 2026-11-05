/**
 * Cookie consent helpers - describes which buckets we record and the
 * default state. The actual choice lives in the `cookie_consent`
 * table keyed by user email.
 */

export interface CookieConsent {
  necessary: boolean;
  analytics: boolean;
  marketing: boolean;
  preferences: boolean;
}

export const defaultCookieConsent: CookieConsent = {
  necessary: true,
  analytics: false,
  marketing: false,
  preferences: false,
};
