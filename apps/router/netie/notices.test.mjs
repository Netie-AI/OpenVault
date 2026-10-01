// Copyright (c) 2026 Netie AI. MIT.
//
// The /notices page, NETIE_NOTICES.md, LICENSE and the package "files" list
// must agree, so every FreeRoute install carries the upstream license texts.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { UPSTREAM_NOTICES } from "../src/app/notices/upstreamLicenses.ts";

const ROOT = new URL("..", import.meta.url).pathname;
const read = (rel) => readFileSync(join(ROOT, rel), "utf8");

test("the notices page lists OmniRoute, 9router and CLIProxyAPI", () => {
  assert.deepEqual(
    UPSTREAM_NOTICES.map((n) => n.name),
    ["OmniRoute", "9router", "CLIProxyAPI"]
  );
});

test("the OmniRoute license on /notices is the upstream LICENSE, verbatim", () => {
  const omni = UPSTREAM_NOTICES.find((n) => n.name === "OmniRoute");
  assert.equal(omni.licenseText, read("LICENSE").replace(/\n+$/, ""));
  assert.match(omni.licenseText, /Copyright \(c\) 2026 diegosouzapw/);
});

test("the 9router and CLIProxyAPI licenses match NETIE_NOTICES.md and OpenVault's root notices", () => {
  const netie = read("NETIE_NOTICES.md");
  const rootNotices = read("../../THIRD_PARTY_NOTICES.md");
  for (const name of ["9router", "CLIProxyAPI"]) {
    const notice = UPSTREAM_NOTICES.find((n) => n.name === name);
    assert.ok(notice.licenseText.startsWith("MIT License"), `${name} text starts with MIT License`);
    assert.ok(netie.includes(notice.licenseText), `${name} text is in NETIE_NOTICES.md`);
    assert.ok(rootNotices.includes(notice.licenseText), `${name} text is in the root notices`);
  }
  assert.match(netie, /Copyright \(c\) 2024-2026 decolua and contributors/);
  assert.match(netie, /Copyright \(c\) 2025\.9-present Router-For\.ME/);
});

test("package.json ships the notices and keeps the upstream author", () => {
  const pkg = JSON.parse(read("package.json"));
  for (const entry of ["LICENSE", "THIRD_PARTY_NOTICES.md", "NETIE_NOTICES.md", "licenses/"]) {
    assert.ok(pkg.files.includes(entry), `"files" includes ${entry}`);
  }
  assert.ok(!pkg.files.includes("@omniroute/"), `"files" no longer lists the pruned @omniroute/ dir`);
  assert.equal(pkg.author, "diegosouzapw");
  for (const rel of [
    "licenses/@omniroute/opencode-plugin/LICENSE",
    "licenses/@omniroute/opencode-plugin-v2/LICENSE",
    "licenses/@omniroute/opencode-provider/LICENSE",
  ]) {
    assert.ok(existsSync(join(ROOT, rel)), `${rel} is kept`);
  }
});
