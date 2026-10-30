import type { WorkspaceDetector } from "./types";

/** Strip a UTF-8 BOM if present (Windows-saved manifests). */
function stripBom(content: string): string {
  return content.charCodeAt(0) === 0xfeff ? content.slice(1) : content;
}

/**
 * Parse a `pnpm-workspace.yaml` `packages:` block into raw patterns.
 * Avoids a YAML dependency - we only need a single list under one key.
 */
function parsePnpmWorkspaceYaml(content: string): string[] {
  const patterns: string[] = [];
  let inPackagesBlock = false;

  for (const rawLine of stripBom(content).split("\n")) {
    const line = rawLine.replace(/#.*$/, "").trimEnd();
    if (!line) continue;

    if (!inPackagesBlock) {
      // Match `packages:` at any indent. Other top-level keys (e.g. `catalog:`) skip us out below.
      if (/^\s*packages\s*:\s*$/.test(line)) {
        inPackagesBlock = true;
      }
      continue;
    }

    // Another top-level key - packages block ended.
    if (/^[A-Za-z0-9_-]+\s*:/.test(line)) {
      break;
    }

    const match = line.match(/^\s*-\s*['"]?([^'"#]+?)['"]?\s*$/);
    if (match) patterns.push(match[1].trim());
  }

  return patterns;
}

/**
 * Parse a `package.json` `workspaces` field, accepting both the array form
 * (`"workspaces": ["packages/*"]`) and the object form
 * (`"workspaces": { "packages": ["packages/*"] }`).
 */
function parsePackageJsonWorkspaces(content: string): string[] {
  let parsed: unknown;
  try {
    parsed = JSON.parse(stripBom(content));
  } catch {
    return [];
  }
  if (!parsed || typeof parsed !== "object") return [];

  const workspaces = (parsed as { workspaces?: unknown }).workspaces;
  if (Array.isArray(workspaces)) {
    return workspaces.filter(
      (value): value is string => typeof value === "string" && value.trim().length > 0,
    );
  }
  if (workspaces && typeof workspaces === "object") {
    const packages = (workspaces as { packages?: unknown }).packages;
    if (Array.isArray(packages)) {
      return packages.filter(
        (value): value is string => typeof value === "string" && value.trim().length > 0,
      );
    }
  }
  return [];
}

/** pnpm workspaces - the explicit YAML manifest takes precedence over package.json. */
export const pnpmWorkspaceDetector: WorkspaceDetector = {
  id: "pnpm",
  label: "pnpm",
  manifestFiles: ["pnpm-workspace.yaml"],
  packageManager: "pnpm",
  parseSubProjects: parsePnpmWorkspaceYaml,
};

/** npm / yarn / bun workspaces - package.json `workspaces` field at the repo root. */
export const npmWorkspaceDetector: WorkspaceDetector = {
  id: "npm-workspaces",
  label: "npm workspaces",
  manifestFiles: ["package.json"],
  packageManager: "npm",
  parseSubProjects: parsePackageJsonWorkspaces,
};
