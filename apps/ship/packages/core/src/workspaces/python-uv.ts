import type { WorkspaceDetector } from "./types";
import { extractStringArrayFromSection } from "./toml-helpers";

/**
 * uv workspaces - `pyproject.toml` at the repo root contains:
 *
 *     [tool.uv.workspace]
 *     members = ["packages/*"]
 *
 * Returns `[]` for a regular pyproject.toml so a single-package Python repo
 * doesn't get misclassified as a monorepo.
 */
function parseUvPyproject(content: string): string[] {
  return extractStringArrayFromSection(content, "tool.uv.workspace", "members");
}

export const uvWorkspaceDetector: WorkspaceDetector = {
  id: "uv",
  label: "uv",
  manifestFiles: ["pyproject.toml"],
  packageManager: "uv",
  parseSubProjects: parseUvPyproject,
};
