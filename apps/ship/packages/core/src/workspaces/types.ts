/**
 * Workspace detector registry - one entry per monorepo family.
 *
 * Each detector knows how to recognize one kind of workspace manifest
 * (pnpm-workspace.yaml, Cargo.toml with [workspace], go.work, *.sln, …)
 * and how to parse it into a list of sub-project paths or glob patterns.
 *
 * Downstream the project root detector iterates this registry, picks
 * detectors whose manifest is present at the repo root, and uses their
 * output to (a) mark candidate sub-projects as workspace-sourced (b)
 * decide whether the repo is a monorepo at all.
 *
 * Adding a stack family that supports monorepos is exactly one entry here
 * + a test fixture - `project-root-detector` doesn't change.
 */

export interface WorkspaceDetector {
  /** Stable identifier - used in telemetry, logs, and matched-detector annotations. */
  id: string;
  /** Human label shown in UI / logs ("pnpm", "Cargo", ".NET solution"). */
  label: string;
  /**
   * Repo-root files that, if present, trigger this detector. A string is matched
   * by exact lower-cased basename; a RegExp is matched against the basename
   * (used for `*.sln` where the filename is project-specific).
   */
  manifestFiles: ReadonlyArray<string | RegExp>;
  /**
   * Optional package manager / toolchain hint. JS detectors set this so the
   * upstream workspace-context rewriter ("cd ../.. && pnpm install") can pick
   * the right command. Non-JS detectors (cargo, go.work, .sln) leave this
   * undefined - their build tools resolve the workspace implicitly so no
   * command rewriting is needed.
   */
  packageManager?: string;
  /**
   * Parse one matching manifest's text content into a list of sub-project
   * paths or glob patterns. Return `[]` when the file exists but doesn't
   * actually declare a workspace (e.g. a regular Cargo.toml without
   * `[workspace]`, or a pyproject.toml without `[tool.uv.workspace]`).
   *
   * Patterns may be:
   *   - literal paths (`"app"`, `"services/api"`)
   *   - globs (`"packages/*"`, `"apps/**"`)
   * The downstream matcher in project-root-detector handles both.
   */
  parseSubProjects(content: string): string[];
}

/**
 * Result of resolving a workspace at a given repo root.
 * - `detector` is the matched detector entry.
 * - `patterns` is the (possibly empty) list of sub-project paths/globs the manifest declares.
 *
 * Multiple detectors may match the same repo (a JS app that also has a Cargo workspace
 * in a sibling directory), so callers collect *all* matches.
 */
export interface MatchedWorkspace {
  detector: WorkspaceDetector;
  patterns: string[];
}
