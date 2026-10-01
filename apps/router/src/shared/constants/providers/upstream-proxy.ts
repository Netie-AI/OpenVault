/**
 * Provider catalog data — extracted from providers.ts (god-file decomposition).
 * Pure data literal; re-exported by the providers.ts barrel. No behavior change.
 */
export const UPSTREAM_PROXY_PROVIDERS = {
  cliproxyapi: {
    id: "cliproxyapi",
    serviceKinds: [],
    alias: "cpa",
    name: "CLIProxyAPI",
    icon: "proxy",
    color: "#6366F1",
    textIcon: "CPA",
    website: "https://github.com/router-for-me/CLIProxyAPI",
    defaultPort: 8317,
    healthEndpoint: "/v1/models",
    managementPrefix: "/v0/management",
    configDir: "~/.cli-proxy-api",
    binaryName: "cli-proxy-api",
    githubRepo: "router-for-me/CLIProxyAPI",
  },
  // FreeRoute: the embedded 9router service is not shipped (its routes answer
  // 501 upstream_service_not_included, src/lib/netie/hardDisabledRoutes.ts), so
  // it is not listed as a provider either.
};
