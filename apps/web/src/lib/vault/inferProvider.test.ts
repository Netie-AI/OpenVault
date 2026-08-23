/**
 * Provider inference from a pasted secret.
 *
 * Verify manually:
 *   npx tsx src/lib/vault/inferProvider.test.ts
 */

import assert from "node:assert";
import { inferProvider } from "./inferProvider";

const CATALOG = new Set([
  "google",
  "anthropic",
  "openai",
  "openrouter",
  "groq",
  "github_models",
]);

// Google ships two key shapes. `AIza…` is the long-standing one…
assert.equal(inferProvider("AIzaSyD-1234567890abcdefghij", CATALOG).providerId, "google");
// …and AI Studio issues `AQ.…`, which used to fall through to "unrecognised"
// and force the user to pick the provider by hand for a key we fully support.
const studio = inferProvider("AQ.Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0Uv", CATALOG);
assert.equal(studio.providerId, "google");
assert.equal(studio.confidence, "strong");
assert.match(studio.reason, /AI Studio/);

// The new rule must stay narrow: a bare "AQ." prefix is not enough, and it must
// not swallow keys belonging to other vendors.
assert.equal(inferProvider("AQ.short", CATALOG).providerId, null);
assert.equal(inferProvider("sk-ant-abc123def456ghi789", CATALOG).providerId, "anthropic");
assert.equal(inferProvider("sk-or-v1-abc123def456ghi789", CATALOG).providerId, "openrouter");
assert.equal(inferProvider("gsk_abc123def456ghi789", CATALOG).providerId, "groq");

// Still honest when it cannot tell.
assert.equal(inferProvider("just-some-text", CATALOG).providerId, null);
assert.equal(inferProvider("", CATALOG).confidence, "none");

// A vendor we recognise but cannot vault must not prefill a rejected provider.
const notInCatalog = inferProvider("AQ.Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0Uv", new Set(["openai"]));
assert.equal(notInCatalog.providerId, null);
assert.match(notInCatalog.reason, /not in the catalog/);

console.log("inferProvider: all assertions passed");
