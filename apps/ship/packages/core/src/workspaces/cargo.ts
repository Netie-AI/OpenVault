import type { WorkspaceDetector } from "./types";
import { extractStringArrayFromSection } from "./toml-helpers";

/**
 * Cargo workspaces - `Cargo.toml` at the repo root contains a `[workspace]`
 * section with a `members = [...]` list of paths or globs.
 *
 *     [workspace]
 *     members = ["crates/*", "examples/foo"]
 *     resolver = "2"
 *
 * Returns `[]` for a regular `Cargo.toml` (no `[workspace]` section), so the
 * detector cleanly distinguishes a single-crate repo from a workspace one.
 */
function parseCargoToml(content: string): string[] {
  return extractStringArrayFromSection(content, "workspace", "members");
}

export const cargoWorkspaceDetector: WorkspaceDetector = {
  id: "cargo",
  label: "Cargo",
  manifestFiles: ["Cargo.toml"],
  // No packageManager - `cargo build` resolves workspaces implicitly from any subdir,
  // so the workspace-context command rewriter doesn't apply here.
  parseSubProjects: parseCargoToml,
};
