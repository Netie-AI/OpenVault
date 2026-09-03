import type { RouteClassification } from "./types";

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

function normalizePathname(rawPath: string): string {
  let path = rawPath || "/";
  if (!path.startsWith("/")) path = `/${path}`;
  if (path.length > 1 && path.endsWith("/")) path = path.slice(0, -1);
  return path;
}

function stripOvApiPrefix(path: string): string {
  if (path === "/ov-api") return "/";
  if (path.startsWith("/ov-api/")) return path.slice("/ov-api".length) || "/";
  return path;
}

function isStaticAsset(path: string): boolean {
  return (
    path.startsWith("/_next/static") ||
    path.startsWith("/_next/image") ||
    path === "/favicon.ico"
  );
}

export function classifyRoute(rawPath: string, method: string = "GET"): RouteClassification {
  const pathname = normalizePathname(rawPath);
  const normalizedPath = stripOvApiPrefix(pathname);
  const upperMethod = method.toUpperCase();

  if (isStaticAsset(pathname)) {
    return {
      routeClass: "PUBLIC",
      reason: "static_asset",
      normalizedPath: pathname,
    };
  }

  if (pathname === "/ov-api/v1" || pathname.startsWith("/ov-api/v1/")) {
    return {
      routeClass: "CLIENT_API",
      reason: "client_api_v1",
      normalizedPath,
    };
  }

  if (pathname === "/ov-api/api" || pathname.startsWith("/ov-api/api/")) {
    if (SAFE_METHODS.has(upperMethod)) {
      return {
        routeClass: "CLIENT_API",
        reason: "client_api_read",
        normalizedPath,
      };
    }
    return {
      routeClass: "MANAGEMENT",
      reason: "management_api_mutation",
      normalizedPath,
    };
  }

  if (pathname.startsWith("/ov-api/")) {
    return {
      routeClass: SAFE_METHODS.has(upperMethod) ? "CLIENT_API" : "MANAGEMENT",
      reason: SAFE_METHODS.has(upperMethod) ? "client_api_read" : "management_api_mutation",
      normalizedPath,
    };
  }

  return {
    routeClass: "MANAGEMENT",
    reason: pathname === "/" ? "management_page" : "fallback_management",
    normalizedPath: pathname,
  };
}
