import { firstServicePort } from "@repo/core";
import type { Service, ServiceInput } from "@/lib/api/services";
import { resolvePublicEndpointHostname } from "./public-endpoint-payload";

export type ServiceEndpoint = NonNullable<Service["publicEndpoints"]>[number];

function validPort(value: unknown): number | null {
  const port = Number(value);
  return Number.isInteger(port) && port > 0 && port <= 65535 ? port : null;
}

/** Saved routes, including paused ones. Never invent a free hostname. */
export function configuredServiceEndpoints(service: Service): ServiceEndpoint[] {
  const routes = service.publicEndpoints?.length
    ? service.publicEndpoints
    : [{
        port: validPort(service.exposedPort) ?? firstServicePort(service.ports ?? []) ?? 0,
        domainType: service.domainType === "custom" ? "custom" as const : "free" as const,
        domain: service.domain ?? undefined,
        customDomain: service.customDomain ?? undefined,
      }];
  return routes.flatMap((route) => {
    const port = validPort(route.port);
    const hostname = route.domainType === "custom" ? route.customDomain?.trim() : route.domain?.trim();
    if (!port || !hostname) return [];
    return [{
      port,
      domainType: route.domainType,
      ...(route.domainType === "custom" ? { customDomain: hostname } : { domain: hostname }),
    }];
  });
}

/** Both the full set and its primary scalars must agree, including clearing the last route. */
export function serviceEndpointsPatch(endpoints: ServiceEndpoint[], exposed = true): Partial<ServiceInput> {
  const primary = endpoints[0];
  return {
    publicEndpoints: endpoints,
    exposed: exposed && endpoints.length > 0,
    exposedPort: primary ? String(primary.port) : "",
    domainType: primary?.domainType ?? "free",
    domain: primary?.domain ?? "",
    customDomain: primary?.customDomain ?? "",
  };
}

export interface ServicePortTarget {
  key: string;
  port: number | null;
  label: string;
  protocol: string;
  bindings: string[];
  endpoints: Array<ServiceEndpoint & { hostname: string }>;
}

/** Display and domain actions use container ports, never the host-side binding. */
export function servicePortTargets(service: Service, baseDomain: string): ServicePortTarget[] {
  const targets = new Map<string, ServicePortTarget>();
  for (const raw of service.ports ?? []) {
    const spec = raw.trim();
    if (!spec) continue;
    const last = spec.split(":").at(-1)!;
    const [label, protocol = "tcp"] = last.split("/");
    const key = `${label}/${protocol.toLowerCase()}`;
    const target = targets.get(key) ?? {
      key, port: validPort(label), label, protocol: protocol.toLowerCase(), bindings: [], endpoints: [],
    };
    if (!target.bindings.includes(spec)) target.bindings.push(spec);
    targets.set(key, target);
  }
  // Some source-built services have only an exposedPort, without a Compose ports list.
  const exposedPort = validPort(service.exposedPort);
  const endpoints = configuredServiceEndpoints(service);
  for (const port of [...(exposedPort ? [exposedPort] : []), ...endpoints.map((endpoint) => endpoint.port)]) {
    const key = `${port}/tcp`;
    if (!targets.has(key)) targets.set(key, {
      key, port, label: String(port), protocol: "tcp", bindings: [], endpoints: [],
    });
  }
  for (const endpoint of endpoints) {
    const hostname = resolvePublicEndpointHostname(endpoint, baseDomain);
    if (hostname) targets.get(`${endpoint.port}/tcp`)?.endpoints.push({ ...endpoint, hostname });
  }
  return [...targets.values()];
}
