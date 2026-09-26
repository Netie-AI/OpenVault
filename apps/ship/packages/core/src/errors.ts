/**
 * Shared error classes used across the monorepo.
 */
// Modified by Netie AI, 2026: added HostedCloudDisabledError and
// KeysManagedByOpenVaultError for FreeBuild's self-hosted edition — see
// packages/core/src/netie/keyvault.ts and apps/api/src/middleware/error-handler.ts.

export class AppError extends Error {
  constructor(
    message: string,
    public statusCode: number = 500,
    public code?: string,
  ) {
    super(message);
    this.name = "AppError";
  }
}

export class NotFoundError extends AppError {
  constructor(resource: string, id?: string) {
    super(id ? `${resource} '${id}' not found` : `${resource} not found`, 404, "NOT_FOUND");
    this.name = "NotFoundError";
  }
}

export class UnauthorizedError extends AppError {
  constructor(message = "Unauthorized") {
    super(message, 401, "UNAUTHORIZED");
    this.name = "UnauthorizedError";
  }
}

export class ForbiddenError extends AppError {
  constructor(message = "Forbidden") {
    super(message, 403, "FORBIDDEN");
    this.name = "ForbiddenError";
  }
}

export class ValidationError extends AppError {
  constructor(message: string, public details?: Record<string, string[]>) {
    super(message, 400, "VALIDATION_ERROR");
    this.name = "ValidationError";
  }
}

export class ConflictError extends AppError {
  constructor(message: string) {
    super(message, 409, "CONFLICT");
    this.name = "ConflictError";
  }
}

/**
 * The host that runs this resource could not be reached — SSH refused our key,
 * the box is down, or the host channel isn't there.
 *
 * 503, deliberately, and NOT the 400 these paths used to return: the request was
 * valid and the infrastructure wasn't, so a client error misdirects the operator
 * to their own input. The message carries the underlying transport reason (which
 * already names the target and what to check), and the code lets the dashboard
 * branch on the class of failure rather than on copy.
 */
export class HostUnreachableError extends AppError {
  constructor(message: string) {
    super(message, 503, "HOST_UNREACHABLE");
    this.name = "HostUnreachableError";
  }
}

/**
 * Deployment-specific error with a machine-readable code.
 *
 * Codes:
 *   PORT_IN_USE - target port is occupied by another process
 *   FOREIGN_COMPOSE_STACK - an unowned same-project Compose stack exists on the target
 */
export class DeployError extends AppError {
  constructor(
    message: string,
    code: string,
    public details?: Record<string, unknown>,
  ) {
    super(message, 500, code);
    this.name = "DeployError";
  }
}

/**
 * A feature that depends on Openship Cloud (the Oblien-hosted SaaS) — team-mode
 * cloud/tunnel migration, the Cloud connect bridge, the cloud-only billing and
 * analytics proxy, etc. FreeBuild is self-hosted only (PRODUCT_ROLES.md), so
 * every one of those routes answers this instead of reaching the upstream
 * vendor. `code` is always "hosted_cloud_disabled" — see
 * apps/api/src/middleware/error-handler.ts for the nested `{error:{code,
 * message}}` wire shape this maps to.
 */
export class HostedCloudDisabledError extends AppError {
  constructor(
    message = "The hosted cloud service is not available in this self-hosted edition.",
  ) {
    super(message, 501, "hosted_cloud_disabled");
    this.name = "HostedCloudDisabledError";
  }
}

/**
 * A route tried to save a third-party provider API key into FreeBuild's own
 * store. FreeBuild reads provider keys from OpenVault's KeyVault only (see
 * packages/core/src/netie/keyvault.ts) and never persists them itself.
 * `code` is always "keys_managed_by_openvault".
 */
export class KeysManagedByOpenVaultError extends AppError {
  constructor(openVaultKeysUrl = "http://127.0.0.1:3010/keys") {
    super(
      `Provider keys are stored in OpenVault. Add them at ${openVaultKeysUrl}.`,
      501,
      "keys_managed_by_openvault",
    );
    this.name = "KeysManagedByOpenVaultError";
  }
}

/**
 * Extract a safe string description from an unknown caught value.
 *
 * Why: ssh2, the AWS SDK, and other libraries attach credentials and
 * full request/response objects to their Error subclasses. Passing
 * those Error objects to `console.error` logs the entire object graph
 * — including private keys, signed headers, and bucket configs — into
 * log aggregators (Datadog, Loki, sometimes shared with vendors).
 *
 * `err.message` strips the structured fields and keeps only the
 * human-readable string. Non-Error values fall through to `String()`
 * so we never throw inside a catch block.
 *
 * Bound to 2000 chars so a deeply nested message can't bloat log
 * entries.
 */
export function safeErrorMessage(err: unknown): string {
  if (err instanceof Error) {
    return err.message.slice(0, 2000);
  }
  return String(err).slice(0, 2000);
}
