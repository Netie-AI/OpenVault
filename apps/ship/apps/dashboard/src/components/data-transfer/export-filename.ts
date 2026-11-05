// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
//
// The name the browser gives a data export download. It lives in its own module,
// with no imports, so netie/e2e-openvault.mjs can load this exact function and
// check the name a user sees, instead of keeping a copy of the template.

export type ExportDownloadScope = "instance" | "projects";

/**
 * `freebuild-<label>-<timestamp>.json`. The label is the project name when one
 * is given, otherwise `instance` or `projects`; every run of characters outside
 * letters, digits, underscore and dash becomes a single dash.
 */
export function exportDownloadFilename(opts: {
  scope: ExportDownloadScope;
  projectName?: string;
  now?: Date;
}): string {
  const label = (opts.projectName ?? (opts.scope === "projects" ? "projects" : "instance")).replace(
    /[^a-z0-9_-]+/gi,
    "-",
  );
  const filename = `freebuild-${label}-${(opts.now ?? new Date()).toISOString().replace(/[:.]/g, "-")}.json`;
  return filename;
}
