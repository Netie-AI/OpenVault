/**
 * Tunneling — generic port-exposure primitive.
 *
 * A tunnel takes a local port on this machine and exposes it at a
 * public URL via a provider (Oblien, ngrok, Cloudflare, ...). Each
 * provider implements TunnelProvider; the rest of the app talks to
 * the generic surface in ./service and ./manager.
 *
 * Today's only consumer is team-mode Path C (expose this dashboard
 * via Oblien). Future consumers: user-defined tunnels (operator
 * exposes a dev server / webhook receiver), service deployments
 * with "expose via tunnel" toggle, ephemeral preview URLs.
 */

/**
 * Provider id. Adding a provider is two steps:
 *   1. Implement TunnelProvider in providers/<name>.provider.ts
 *   2. Register in registry.ts
 */
export type TunnelProviderName = "oblien" | "ngrok" | "cloudflare";

export interface TunnelProvisionInput {
  /** Display name attached to the provider record (operator-facing). */
  name: string;
  /** Local port the agent will forward traffic to. */
  port: number;
  /**
   * Provider-specific subdomain hint. Oblien honors this; ngrok free
   * and Cloudflare quick-tunnel ignore it (they generate random URLs).
   * Caller MUST treat the returned record.slug as the source of truth.
   */
  slug?: string;
  /**
   * Opaque payload forwarded to the provider. Lets specific providers
   * pick up data they need without polluting the generic surface
   * (e.g. Oblien needs organizationId to resolve a namespace).
   */
  context?: Record<string, unknown>;
}

export interface TunnelRecord {
  /** Provider's own id for this tunnel. Pass back to delete()/connect(). */
  externalId: string;
  /** Final slug as the provider accepted it (may differ from request). */
  slug: string;
  /** Public URL that routes incoming traffic to the agent. */
  publicUrl: string;
}

/**
 * Long-lived agent that forwards traffic from the public URL to the
 * configured local port. Lifecycle is managed by ./manager; providers
 * only have to return one of these from connect().
 */
export interface TunnelAgent {
  close(): void;
  readonly isConnected: boolean;
  on(event: "disconnect", listener: (code: number, reason: string) => void): unknown;
  on(event: "error", listener: (err: Error) => void): unknown;
  on(event: "close", listener: () => void): unknown;
}

export interface TunnelProvider {
  readonly name: TunnelProviderName;
  /**
   * Quick readiness check — credentials present, env configured,
   * outbound network reachable. Called by service.provisionTunnel
   * before any state-mutating provider call.
   */
  preflight(): Promise<{ ok: true } | { ok: false; reason: string }>;
  /**
   * Provision the tunnel record on the provider side. Returns the
   * data the caller should persist (externalId/slug/publicUrl).
   */
  create(input: TunnelProvisionInput): Promise<TunnelRecord>;
  /** Permanently delete the tunnel record. Best-effort — should be idempotent. */
  delete(externalId: string): Promise<void>;
  /**
   * Open a long-lived agent connecting the public URL to the given
   * local port. Returns an agent the manager will own and reconnect
   * if the socket drops. Called on API boot and after provisioning.
   */
  connect(record: TunnelRecord, port: number): Promise<TunnelAgent>;
}

/* ────── Typed errors ───────────────────────────────────────────── */

export class UnknownProviderError extends Error {
  readonly code = "TUNNEL_UNKNOWN_PROVIDER" as const;
  constructor(name: string) {
    super(`Unknown tunnel provider: ${name}`);
    this.name = "UnknownProviderError";
  }
}

export class ProviderNotImplementedError extends Error {
  readonly code = "TUNNEL_PROVIDER_NOT_IMPLEMENTED" as const;
  constructor(public readonly provider: TunnelProviderName) {
    super(
      `Tunnel provider "${provider}" is not yet implemented. Pick another provider for now.`,
    );
    this.name = "ProviderNotImplementedError";
  }
}

export class ProviderNotReadyError extends Error {
  readonly code = "TUNNEL_PROVIDER_NOT_READY" as const;
  constructor(public readonly provider: TunnelProviderName, reason: string) {
    super(`Tunnel provider "${provider}" not ready: ${reason}`);
    this.name = "ProviderNotReadyError";
  }
}

export class SlugTakenError extends Error {
  readonly code = "TUNNEL_SLUG_TAKEN" as const;
  constructor(public readonly provider: TunnelProviderName, public readonly slug: string) {
    super(`Slug "${slug}" is already taken on the ${provider} provider.`);
    this.name = "SlugTakenError";
  }
}

export class ProvisionFailedError extends Error {
  readonly code = "TUNNEL_PROVISION_FAILED" as const;
  constructor(public readonly provider: TunnelProviderName, reason: string) {
    super(`Provisioning failed on ${provider}: ${reason}`);
    this.name = "ProvisionFailedError";
  }
}
