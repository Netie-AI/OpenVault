/**
 * Ambient declarations for upstream Zero dependencies we don't ship.
 *
 * - `@intercom/messenger-js-sdk` was the in-app help widget.
 * - `autumn-js` / `autumn-js/react` was the billing/subscriptions SDK.
 * - `@zero/eslint-config` is a workspace package not present here.
 * - `brain.fallback.prompts` / `server/src/types` referenced AI agent
 *    code we removed when stripping the LLM dependency.
 *
 * Every export is typed `any` so the legacy imports compile and consumers
 * type-check. Runtime values are `undefined`; the corresponding UI is
 * either gated off or has been replaced with a no-op.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */

declare module '@intercom/messenger-js-sdk' {
  const Intercom: any;
  export const show: any;
  export const hide: any;
  export const update: any;
  export default Intercom;
}

declare module 'autumn-js' {
  export type Customer = any;
  export type Product = any;
  const value: any;
  export default value;
}

declare module 'autumn-js/react' {
  export const useAutumn: any;
  export const useCustomer: any;
  export const AutumnProvider: any;
  const value: any;
  export default value;
}

declare module '@zero/eslint-config' {
  const config: any;
  export default config;
}

declare module '*brain.fallback.prompts' {
  export const SummarizeMessage: string;
  export const SummarizeThread: string;
  export const ReSummarizeThread: string;
}

declare module '*server/src/types' {
  export const EPrompts: any;
  export type EPrompts = any;
  export type Tools = any;
}
