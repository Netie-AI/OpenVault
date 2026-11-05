// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
//
// Preload for the smoke test (node --import netie/fetch-log.mjs): records every
// URL the API process passes to fetch() in $FREEBUILD_FETCH_LOG, and refuses the
// upstream project's repositories and registry outright. The smoke test then
// asserts that nothing in a default boot tried to reach them (DR-0018 hard line 5).
import { appendFileSync } from "node:fs";

const logPath = process.env.FREEBUILD_FETCH_LOG;
const UPSTREAM =
  /(raw\.githubusercontent\.com|github\.com|api\.github\.com\/repos|ghcr\.io)\/(oblien|openship)\b|(^|\.)opsh\.io\b/i;
const realFetch = globalThis.fetch;

globalThis.fetch = async (input, init) => {
  const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  if (logPath) appendFileSync(logPath, `${url}\n`);
  if (UPSTREAM.test(url)) throw new TypeError(`blocked fetch to the upstream project: ${url}`);
  return realFetch(input, init);
};
