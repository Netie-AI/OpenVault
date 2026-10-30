/**
 * Mail-server routing - api-side barrel.
 *
 * Public surface:
 *   - Pure types + `buildMailServerRoutes` re-exported from `@repo/core`
 *     for convenience (so callers in apps/api can import them from one place).
 *   - Side-effecting `registerMailServerRoutes` / `removeMailServerRoutes` /
 *     `rotateMailServerRoutes` that call openship's routing provider.
 *
 * Consumed by:
 *   - The mail-server provisioning flow (registers routes on install)
 *   - The dashboard "Email" page (renders the DNS record list from `plan.dns`)
 *   - The deprovisioning flow (cleans up routes on uninstall)
 */

// Pure types + builder - owned by @repo/core, re-exported here for one-stop importing.
export {
  buildMailServerRoutes,
  type MailServerRouteInput,
  type MailServerRoutePlan,
  type MailRoute,
  type MailRouteId,
  type MailDnsRecord,
  type MailDnsRecordId,
} from "@repo/core";

// Side-effecting glue - owned here because it calls platform().routing.
export {
  registerMailServerRoutes,
  removeMailServerRoutes,
  rotateMailServerRoutes,
  type MailServerRouteRegistration,
  type RouteRegistrationResult,
} from "./register.service";
