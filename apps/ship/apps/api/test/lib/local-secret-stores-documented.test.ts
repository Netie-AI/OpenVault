// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
// FreeBuild keeps some third-party credentials in its own database (S3 and SFTP backup
// credentials, git clone tokens, and others). DR-0018 and the ship README must say so
// instead of implying every credential lives in OpenVault.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const read = (rel: string) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8");
const dr = read("../../../../../../docs/decisions/DR-0018-fork-router-and-ship.md");
const readme = read("../../../../README.md");

describe("local secret stores are documented", () => {
  it("lists them in DR-0018 under Stores not moved", () => {
    const bullet = dr.slice(dr.indexOf("Stores not moved"));
    for (const phrase of ["S3 and SFTP", "git clone tokens", "per-install", "auth-secret", "keys_managed_by_openvault"]) {
      expect(bullet, phrase).toContain(phrase);
    }
  });

  it("says the same in apps/ship/README.md", () => {
    for (const phrase of ["S3 and SFTP", "git clone tokens", "auth-secret", "database"]) {
      expect(readme, phrase).toContain(phrase);
    }
  });
});
