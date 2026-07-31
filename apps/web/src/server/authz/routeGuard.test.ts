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

console.log("routeGuard smoke: ok");
