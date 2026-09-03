/**
 * Smoke checks for LOCAL_ONLY prefix membership.
 *
 * Run: npx tsx src/server/authz/routeGuard.test.ts
 */

import assert from "node:assert/strict";

import {
  LOCAL_ONLY_API_PREFIXES,
  classifyHostLocality,
  isLocalOnlyPath,
  isLoopbackHost,
} from "./routeGuard";

assert.equal(isLoopbackHost("127.0.0.1"), true);
assert.equal(isLoopbackHost("localhost"), true);
assert.equal(isLoopbackHost("::1"), true);
assert.equal(isLoopbackHost("192.168.1.2"), false);
assert.equal(classifyHostLocality("127.0.0.1"), "loopback");
assert.equal(classifyHostLocality("8.8.8.8"), "remote");
assert.equal(classifyHostLocality(null), "remote");

assert.equal(isLocalOnlyPath("/ov-api/api/ship/run"), true);
assert.equal(isLocalOnlyPath("/api/deploy/plan"), true);
assert.equal(isLocalOnlyPath("/ov-api/api/control/action"), true);
assert.equal(isLocalOnlyPath("/api/openide/invoke"), true);
assert.equal(isLocalOnlyPath("/ov-api/api/engine/candidates/foo"), true);
assert.equal(isLocalOnlyPath("/api/sentinel/run"), true);
assert.equal(isLocalOnlyPath("/ov-api/api/healthz"), false);
assert.equal(isLocalOnlyPath("/ship/page"), false);

assert.equal(LOCAL_ONLY_API_PREFIXES.length, 12);

// Custody surfaces that were reachable from the network the whole time: the
// console rewrites /ov-api/* to 127.0.0.1:5000, so FastAPI's own loopback check
// saw a local peer for every one of these.
assert.equal(isLocalOnlyPath("/ov-api/api/keys"), true);
assert.equal(isLocalOnlyPath("/ov-api/api/keys/abc123/secret"), true);
assert.equal(isLocalOnlyPath("/ov-api/api/secrets"), true);
assert.equal(isLocalOnlyPath("/ov-api/api/vault/unseal"), true);
assert.equal(isLocalOnlyPath("/ov-api/api/apikeys"), true);
assert.equal(isLocalOnlyPath("/ov-api/api/usage"), true);
assert.equal(isLocalOnlyPath("/api/keys"), true);

// Default-deny: a route nobody has thought about yet is local-only, rather
// than exposed until someone remembers to add it to a list.
assert.equal(isLocalOnlyPath("/ov-api/api/some-route-added-next-month"), true);

// ...but the deliberately public ones still work, or the control is just an
// outage (R-0005).
assert.equal(isLocalOnlyPath("/api/healthz"), false);
assert.equal(isLocalOnlyPath("/ov-api/api/mesh/peers"), false);
assert.equal(isLocalOnlyPath("/ov-api/api/peers"), false);

// Next.js pages are not backend routes and must stay reachable.
assert.equal(isLocalOnlyPath("/vault"), false);
assert.equal(isLocalOnlyPath("/_next/static/chunk.js"), false);

console.log("routeGuard smoke: ok");
