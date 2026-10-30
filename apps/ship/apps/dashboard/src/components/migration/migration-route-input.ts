import type { MigrationRouteSpec } from "@repo/contracts";
import { createPublicEndpoint, type PublicEndpoint } from "@/context/deployment/types";
import type { DiscoveredService } from "@/lib/api/server-migration";

/** Keep every detected hostname/path and the matched container listen port. */
export function keptServiceRoutes(
  service: DiscoveredService,
  fallbackPort: string,
): PublicEndpoint[] {
  return (service.existingRoute ?? []).flatMap((route) =>
    route.domains.map((domain) =>
      createPublicEndpoint({
        port: String(route.containerPort ?? fallbackPort),
        domainType: "custom",
        customDomain: domain,
        ...(route.path && (route.path !== "/" || route.exact) ? { targetPath: route.path } : {}),
        ...(route.exact ? { exact: true } : {}),
      }),
    ),
  );
}

/** Preserve all reviewed routes across the dashboard → engine boundary. */
export function toServerRoutes(
  routes: Record<string, PublicEndpoint[]> | undefined,
): Record<string, MigrationRouteSpec[]> | undefined {
  if (!routes) return undefined;
  const out: Record<string, MigrationRouteSpec[]> = {};
  for (const [key, endpoints] of Object.entries(routes)) {
    const selected = endpoints.flatMap((endpoint): MigrationRouteSpec[] => {
      const domain = (endpoint.domainType === "custom" ? endpoint.customDomain : endpoint.domain)
        ?.trim()
        .toLowerCase();
      if (!domain) return [];
      const targetPath = endpoint.targetPath?.trim();
      return [
        {
          domainType: endpoint.domainType === "custom" ? "custom" : "free",
          ...(endpoint.domainType === "custom" ? { customDomain: domain } : { domain }),
          ...(endpoint.port ? { exposedPort: String(endpoint.port) } : {}),
          ...(targetPath && (targetPath !== "/" || endpoint.exact) ? { targetPath } : {}),
          ...(endpoint.exact ? { exact: true } : {}),
        },
      ];
    });
    if (selected.length) out[key] = selected;
  }
  return Object.keys(out).length ? out : undefined;
}
