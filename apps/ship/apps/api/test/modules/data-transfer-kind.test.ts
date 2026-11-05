// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
// An export is stamped with FreeBuild names. An archive written by Openship, or by a
// FreeBuild build before the rename, still imports: the importer reads both families of
// names and the exporter writes only the new one.
import { describe, expect, it } from "vitest";

import { assertValidEnvelope } from "../../src/modules/system/data-transfer/import.service";
import {
  isInstanceExportKind,
  isProjectExportKind,
  type DataTransferFile,
} from "../../src/modules/system/data-transfer/types";

function archive(kind: string, scope: "instance" | "project"): DataTransferFile {
  return {
    kind,
    envelopeVersion: 2,
    createdAt: "2026-10-30T00:00:00.000Z",
    sourceDriver: "pglite",
    dump: { formatVersion: 1, scope: { kind: scope }, tables: {} },
  } as unknown as DataTransferFile;
}

describe("export kind names", () => {
  it.each([
    ["freebuild-instance-export", "instance"],
    ["openship-instance-export", "instance"],
    ["freebuild-project-export", "project"],
    ["openship-project-export", "project"],
  ] as const)("accepts %s", (kind, scope) => {
    expect(() => assertValidEnvelope(archive(kind, scope))).not.toThrow();
  });

  it("rejects a kind from neither family", () => {
    expect(() => assertValidEnvelope(archive("somebody-else-export", "instance"))).toThrow(
      "Not a FreeBuild export file.",
    );
  });

  it.each([
    ["freebuild-instance-export", "project"],
    ["openship-instance-export", "project"],
    ["freebuild-project-export", "instance"],
    ["openship-project-export", "instance"],
  ] as const)("rejects %s whose contents are a %s scope", (kind, scope) => {
    expect(() => assertValidEnvelope(archive(kind, scope))).toThrow(
      "The export scope does not match its contents.",
    );
  });

  it("classifies both families the same way", () => {
    expect(isInstanceExportKind("freebuild-instance-export")).toBe(true);
    expect(isInstanceExportKind("openship-instance-export")).toBe(true);
    expect(isInstanceExportKind("freebuild-project-export")).toBe(false);
    expect(isProjectExportKind("freebuild-project-export")).toBe(true);
    expect(isProjectExportKind("openship-project-export")).toBe(true);
    expect(isProjectExportKind("openship-instance-export")).toBe(false);
    expect(isProjectExportKind("")).toBe(false);
  });
});
