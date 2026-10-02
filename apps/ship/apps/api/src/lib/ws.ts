/**
 * WebSocket helpers - a thin wrapper around @hono/node-ws.
 *
 * The handshake `upgradeWebSocket` factory is bound to a specific Hono
 * instance, and that binding must happen AFTER `app.ts` has finished
 * constructing the Hono instance. To avoid an import cycle (this file
 * would otherwise pull `app` from `../app`, but `../app` is mid-load
 * when route modules first import this file), we use a deferred init:
 *
 *   1. `app.ts`: `setupWebSocket(app)` immediately after `new Hono()`.
 *   2. Route modules: `upgradeWebSocket(...)` at module load — safe
 *      because step 1 already ran.
 *   3. `index.ts`: `injectWebSocket(server)` after `serve()` returns
 *      the http.Server handle.
 */
import { createNodeWebSocket } from "@hono/node-ws";
import type { Hono } from "hono";

type NodeWs = ReturnType<typeof createNodeWebSocket>;
type UpgradeFn = NodeWs["upgradeWebSocket"];
type InjectFn = NodeWs["injectWebSocket"];

let _upgrade: UpgradeFn | null = null;
let _inject: InjectFn | null = null;

export function setupWebSocket(app: Hono): void {
  if (_upgrade) return; // idempotent — guards against re-init in HMR
  const ws = createNodeWebSocket({ app });
  _upgrade = ws.upgradeWebSocket;
  _inject = ws.injectWebSocket;
}

export const upgradeWebSocket: UpgradeFn = ((...args: Parameters<UpgradeFn>) => {
  if (!_upgrade) {
    throw new Error(
      "[ws] upgradeWebSocket called before setupWebSocket(app) - check that app.ts initializes WS before mounting routes.",
    );
  }
  return _upgrade(...args);
}) as UpgradeFn;

export const injectWebSocket: InjectFn = ((server) => {
  if (!_inject) {
    throw new Error("[ws] injectWebSocket called before setupWebSocket(app).");
  }
  return _inject(server);
}) as InjectFn;
