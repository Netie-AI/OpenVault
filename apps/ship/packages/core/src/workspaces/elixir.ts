import type { WorkspaceDetector } from "./types";

/**
 * Elixir umbrella projects - `mix.exs` at the repo root sets
 * `apps_path: "apps"`, which makes every directory under `apps/` a
 * sub-application:
 *
 *     defmodule MyUmbrella.MixProject do
 *       use Mix.Project
 *       def project do
 *         [apps_path: "apps", …]
 *       end
 *     end
 *
 * Returns `["<apps_path>/*"]` (a glob) so the downstream matcher discovers
 * every concrete sub-app at scan time. Returns `[]` for a regular non-umbrella
 * mix project.
 */
function parseMixExs(content: string): string[] {
  // Match `apps_path: "apps"` (or `apps_path: "apps/"`), tolerating whitespace
  // and either single- or double-quoted strings. mix.exs is Elixir source, but
  // this one key is conventional enough to match by regex.
  const match = content.match(/apps_path\s*:\s*["']([^"']+)["']/);
  if (!match) return [];
  const appsPath = match[1].trim().replace(/^\/+|\/+$/g, "");
  if (!appsPath) return [];
  return [`${appsPath}/*`];
}

export const elixirUmbrellaDetector: WorkspaceDetector = {
  id: "elixir-umbrella",
  label: "Elixir umbrella",
  manifestFiles: ["mix.exs"],
  packageManager: "mix",
  parseSubProjects: parseMixExs,
};
