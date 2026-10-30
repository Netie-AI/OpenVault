// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
// FreeBuild never restores a provider key that OpenVault manages. An export from an
// upstream install, or from a build before that rule, still carries Cloudflare tokens in
// dns_credential and credential; the import drops those rows and their bundle secrets.
import { describe, expect, it } from "vitest";

import { dropOpenVaultManagedCredentials } from "../../src/modules/system/data-transfer/import.service";
import type { DataTransferFile, SecretBundle } from "../../src/modules/system/data-transfer/types";

function exportFile(tables: Record<string, Array<Record<string, unknown>>>): DataTransferFile {
  return {
    kind: "openship-instance-export",
    envelopeVersion: 2,
    dump: { formatVersion: 1, scope: { kind: "instance" }, tables },
  } as unknown as DataTransferFile;
}

const bundle: SecretBundle = {
  version: 1,
  entries: [
    { table: "dns_credential", id: "dns_1", column: "apiTokenEnc", scheme: "enc1", value: "cf-legacy" },
    { table: "credential", id: "cred_cf", column: "secretsEnc", scheme: "enc1", value: "cf-generic" },
    { table: "credential", id: "cred_reg", column: "secretsEnc", scheme: "enc1", value: "registry" },
    { table: "servers", id: "srv_1", column: "sshPassword", scheme: "enc1", value: "ssh" },
  ],
};

describe("dropOpenVaultManagedCredentials", () => {
  it("drops dns_credential rows and cloudflare credential rows with their secrets", () => {
    const file = exportFile({
      dns_credential: [{ id: "dns_1", provider: "cloudflare", name: "legacy" }],
      credential: [
        { id: "cred_cf", provider: "cloudflare", name: "cf" },
        { id: "cred_reg", provider: "docker-registry", name: "ghcr" },
      ],
      servers: [{ id: "srv_1" }],
    });

    const out = dropOpenVaultManagedCredentials(file, bundle);

    expect(out.dropped).toBe(2);
    expect(out.file.dump.tables.dns_credential).toEqual([]);
    expect(out.file.dump.tables.credential!.map((r) => r.id)).toEqual(["cred_reg"]);
    expect(out.file.dump.tables.servers).toEqual([{ id: "srv_1" }]);
    expect(out.bundle!.entries.map((e) => `${e.table}:${e.id}`)).toEqual([
      "credential:cred_reg",
      "servers:srv_1",
    ]);
    expect(JSON.stringify(out)).not.toContain("cf-legacy");
    expect(JSON.stringify(out)).not.toContain("cf-generic");
  });

  it("does not mutate its inputs", () => {
    const file = exportFile({
      dns_credential: [{ id: "dns_1", provider: "cloudflare" }],
      credential: [{ id: "cred_cf", provider: "cloudflare" }],
    });
    dropOpenVaultManagedCredentials(file, bundle);
    expect(file.dump.tables.dns_credential).toHaveLength(1);
    expect(file.dump.tables.credential).toHaveLength(1);
    expect(bundle.entries).toHaveLength(4);
  });

  it("returns the same objects when there is nothing to drop", () => {
    const file = exportFile({ credential: [{ id: "cred_reg", provider: "docker-registry" }] });
    const only = { version: 1 as const, entries: [bundle.entries[2]!] };
    const out = dropOpenVaultManagedCredentials(file, only);
    expect(out.dropped).toBe(0);
    expect(out.file).toBe(file);
    expect(out.bundle).toBe(only);
  });

  it("drops the rows even when the export carries no secret bundle", () => {
    const file = exportFile({ dns_credential: [{ id: "dns_1", provider: "cloudflare" }] });
    const out = dropOpenVaultManagedCredentials(file, null);
    expect(out.dropped).toBe(1);
    expect(out.bundle).toBeNull();
    expect(out.file.dump.tables.dns_credential).toEqual([]);
  });
});
