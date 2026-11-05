// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
import { describe, expect, it } from "vitest";
import { exportDownloadFilename } from "./export-filename";

const NOW = new Date("2026-10-30T01:02:03.456Z");

describe("exportDownloadFilename", () => {
  it("names an instance export freebuild-instance-<timestamp>.json", () => {
    expect(exportDownloadFilename({ scope: "instance", now: NOW })).toBe(
      "freebuild-instance-2026-10-30T01-02-03-456Z.json",
    );
  });

  it("names a multi-project export freebuild-projects-<timestamp>.json", () => {
    expect(exportDownloadFilename({ scope: "projects", now: NOW })).toBe(
      "freebuild-projects-2026-10-30T01-02-03-456Z.json",
    );
  });

  it("uses the project name, with unsafe runs collapsed to one dash", () => {
    expect(exportDownloadFilename({ scope: "projects", projectName: "My App/v2", now: NOW })).toBe(
      "freebuild-My-App-v2-2026-10-30T01-02-03-456Z.json",
    );
  });

  it("never carries an upstream product name when no project name is given", () => {
    for (const scope of ["instance", "projects"] as const) {
      expect(exportDownloadFilename({ scope, now: NOW })).not.toMatch(/openship|oblien|opsh/i);
    }
  });
});
