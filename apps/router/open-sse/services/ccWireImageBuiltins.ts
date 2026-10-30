/**
 * Built-in provider ids that adopt the dynamic Claude-Code wire image
 * (fingerprint headers/order + system transforms + `?beta=true` chat path)
 * WITHOUT inheriting the Claude-Code-Compatible family's default anthropic
 * baseUrl / Bearer auth. Upstream listed `agentrouter` here (issue #6056).
 *
 * FreeRoute: the Claude Code wire image is client impersonation (the
 * claude-cli user agent, Stainless client headers, the header order of the real
 * client, the CCH billing signature), which is not shipped
 * (tls_fingerprint_stealth_disabled). The set is empty and the switch below is
 * off, so `agentrouter` is an ordinary API-key provider: its registry baseUrl,
 * its `x-api-key`, and honest headers. The `anthropic-compatible-cc-*` custom
 * provider family is refused by name in `open-sse/netie/policy.ts`.
 *
 * Single source of truth, imported by both CC predicates
 * (`isClaudeCodeCompatible` / `isClaudeCodeCompatibleProvider`) so they never
 * diverge.
 */
export const CC_WIRE_IMAGE_BUILTINS: ReadonlySet<string> = new Set<string>();

// `as boolean` keeps the branches that read this flag type-checkable. Do not
// turn it on: it re-enables client impersonation for every CC-shaped provider.
const CC_WIRE_IMAGE_ENABLED = false as boolean;

/** False in this edition: no provider adopts the Claude Code wire image. */
export function isCcWireImageEnabled(): boolean {
  return CC_WIRE_IMAGE_ENABLED;
}

/**
 * True when `provider` is a built-in that adopts the dynamic Claude-Code wire
 * image while keeping its own registry baseUrl + auth. Always false here.
 */
export function usesCcWireImage(provider: unknown): boolean {
  return (
    CC_WIRE_IMAGE_ENABLED && typeof provider === "string" && CC_WIRE_IMAGE_BUILTINS.has(provider)
  );
}
