import { cargoWorkspaceDetector } from "./cargo";
import { dotnetSolutionDetector } from "./dotnet";
import { elixirUmbrellaDetector } from "./elixir";
import { goWorkspaceDetector } from "./go";
import { gradleWorkspaceDetector } from "./gradle";
import { mavenWorkspaceDetector } from "./maven";
import { npmWorkspaceDetector, pnpmWorkspaceDetector } from "./node";
import { uvWorkspaceDetector } from "./python-uv";
import { rushWorkspaceDetector } from "./rush";
import type { WorkspaceDetector } from "./types";

export type { WorkspaceDetector, MatchedWorkspace } from "./types";

/**
 * All registered workspace detectors. Add a new family by:
 *   1. Implementing `WorkspaceDetector` in its own file.
 *   2. Appending it here.
 *   3. Adding 1–2 fixture tests under `apps/api/test/lib/workspaces/`.
 *
 * The order is informational - `project-root-detector` collects ALL matches,
 * not the first, so a polyglot repo (e.g. JS frontend + Cargo workspace) gets
 * both detectors firing.
 */
export const WORKSPACE_DETECTORS: readonly WorkspaceDetector[] = [
  pnpmWorkspaceDetector,
  npmWorkspaceDetector,
  rushWorkspaceDetector,
  cargoWorkspaceDetector,
  goWorkspaceDetector,
  uvWorkspaceDetector,
  elixirUmbrellaDetector,
  mavenWorkspaceDetector,
  gradleWorkspaceDetector,
  dotnetSolutionDetector,
] as const;

/**
 * Lower-cased basename set of every static manifest filename across all
 * detectors, for cheap root-file scans before paying the per-detector
 * `.match()` cost. Regex-based detectors (.sln) are checked separately.
 */
export const WORKSPACE_MANIFEST_FILES: ReadonlySet<string> = new Set(
  WORKSPACE_DETECTORS.flatMap((d) =>
    d.manifestFiles
      .filter((m): m is string => typeof m === "string")
      .map((m) => m.toLowerCase()),
  ),
);

/**
 * Does this manifest filename match any detector?
 *
 * Handles both static-string and regex-based manifest declarations
 * (the latter is used by .sln files where the name varies per repo).
 */
export function findMatchingDetectors(filename: string): WorkspaceDetector[] {
  const lower = filename.toLowerCase();
  return WORKSPACE_DETECTORS.filter((detector) =>
    detector.manifestFiles.some((entry) =>
      typeof entry === "string" ? entry.toLowerCase() === lower : entry.test(filename),
    ),
  );
}

/** Convenience: turn a (filename, content) pair into the patterns it declares (across all matching detectors). */
export function parseWorkspaceManifest(
  filename: string,
  content: string,
): { detector: WorkspaceDetector; patterns: string[] }[] {
  const detectors = findMatchingDetectors(filename);
  return detectors
    .map((detector) => ({ detector, patterns: detector.parseSubProjects(content) }))
    .filter((entry) => entry.patterns.length > 0);
}
