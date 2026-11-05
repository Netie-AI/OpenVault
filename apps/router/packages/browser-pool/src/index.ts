// Copyright (c) 2026 Netie AI. MIT.
//
// @omniroute/browser-pool — hard-disabled in FreeRoute.
//
// This package existed to run a stealth (fingerprint-patched) headless
// Chromium pool backing the *-web browser-session-scraper executors
// (claude-web, duckduckgo-web, grok-web) and their cookie-warmup flow. Those
// executors are hard-disabled in FreeRoute (open-sse/netie/policy.ts —
// `consumer_session_relay_disabled`) before they can ever reach this package,
// and nothing else in this fork imports it. The exports below are kept, typed
// the same as upstream, so a stray import fails loudly with the named 501
// instead of silently launching a browser — rather than deleting the package
// outright and risking an unresolvable import somewhere this audit missed.

import type {
  BrowserBackedChatRequest,
  BrowserBackedChatResult,
  BrowserPoolContextOptions,
  BrowserPoolMetrics,
  PooledContext,
} from "./interfaces.ts";

export type { BrowserPoolContextOptions, BrowserPoolMetrics, PooledContext };

const DISABLED_CODE = "consumer_session_relay_disabled";
const DISABLED_MESSAGE =
  "Relaying a browser chat session (a cookie or session token pasted in place of an " +
  "API key, or an imported/pooled browser login) is disabled in this edition of " +
  "FreeRoute. Connect this provider with a real API key instead.";

class NetieDisabledError extends Error {
  readonly status = 501 as const;
  readonly code = DISABLED_CODE;
  constructor() {
    super(DISABLED_MESSAGE);
    this.name = "NetieDisabledError";
  }
}

function disabled(): never {
  throw new NetieDisabledError();
}

export function acquireBrowserContext(..._args: unknown[]): Promise<PooledContext> {
  return Promise.reject(new NetieDisabledError());
}
export function releaseBrowserContext(..._args: unknown[]): void {
  disabled();
}
export function getBrowserPoolMetrics(): BrowserPoolMetrics {
  disabled();
}
export function readPageResponseBody(..._args: unknown[]): Promise<Buffer> {
  return Promise.reject(new NetieDisabledError());
}
export function openPage(..._args: unknown[]): Promise<unknown> {
  return Promise.reject(new NetieDisabledError());
}
export function shutdownPool(..._args: unknown[]): Promise<void> {
  return Promise.reject(new NetieDisabledError());
}
export function setProxyResolver(..._args: unknown[]): void {
  disabled();
}
export function __resetBrowserPoolMetricsForTest(): void {
  disabled();
}

export function browserBackedChat(
  ..._args: [BrowserBackedChatRequest]
): Promise<BrowserBackedChatResult> {
  return Promise.reject(new NetieDisabledError());
}
export function startBrowserWarmup(..._args: unknown[]): Promise<void> {
  return Promise.reject(new NetieDisabledError());
}
export function getFreshCookiesWithWarmup(..._args: unknown[]): Promise<unknown> {
  return Promise.reject(new NetieDisabledError());
}
export function getCachedCookies(..._args: unknown[]): unknown {
  disabled();
}
export function setCachedCookies(..._args: unknown[]): void {
  disabled();
}
export function clearCookieCache(..._args: unknown[]): void {
  disabled();
}
export function shouldUseGrokBrowserBacked(..._args: unknown[]): boolean {
  return false;
}
export function acquireFreshGrokClearance(..._args: unknown[]): Promise<unknown> {
  return Promise.reject(new NetieDisabledError());
}
