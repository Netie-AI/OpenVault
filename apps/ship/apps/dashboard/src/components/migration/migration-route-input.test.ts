import { describe, expect, it } from "vitest";
import type { DiscoveredService } from "@/lib/api/server-migration";
import { keptServiceRoutes, toServerRoutes } from "./migration-route-input";

describe("migration route request", () => {
  it("sends every reviewed alias, port and path under the selected container's ID", () => {
    const service = {
      existingRoute: [
        {
          port: 18080,
          containerPort: 8080,
          path: "/",
          domains: ["example.com", "www.example.com"],
        },
        { port: 19000, containerPort: 9000, path: "/api", domains: ["example.com"], exact: true },
      ],
    } as DiscoveredService;
    expect(toServerRoutes({ "container-123": keptServiceRoutes(service, "3000") })).toEqual({
      "container-123": [
        { exposedPort: "8080", domainType: "custom", customDomain: "example.com" },
        { exposedPort: "8080", domainType: "custom", customDomain: "www.example.com" },
        {
          exposedPort: "9000",
          domainType: "custom",
          customDomain: "example.com",
          targetPath: "/api",
          exact: true,
        },
      ],
    });
  });
});
