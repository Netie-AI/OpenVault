// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
//
// The webmail (apps/email) came from Openship with Zero (Mail-0) branding. FreeBuild Mail
// must not show it: PWA manifest, Open Graph image, favicons, logo files and the command
// palette text are FreeBuild's (DR-0018 hard line 4). Zero is credited in apps/ship/NOTICE
// only. Internal identifiers (zeroSignature, tool names, code comments) keep their names.
import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const SHIP_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../../../..");
const PUBLIC = join(SHIP_ROOT, "apps/email/client/public");
const read = (rel: string) => readFileSync(join(SHIP_ROOT, rel), "utf8");
const sha256 = (file: string) => createHash("sha256").update(readFileSync(join(PUBLIC, file))).digest("hex");

const ZERO_BRAND = /mail-?0|mail0|\bzero\b/i;

/** sha256 of the brand images in the Openship snapshot (Zero's pixel mark, Zero OG card). */
const UPSTREAM_IMAGE_HASHES: Record<string, string> = {
  "og.png": "440b94956e9b75d3e71be89a0f5766e1afe922d72fbd22bcfa687ef5e7c022e8",
  "favicon.ico": "309195104860766e159ba8012cdc909fa750f6cd113d69f9a012c458d0ce2e04",
  "apple-touch-icon.png": "ddfcf6f3ca15619d80ea4f7fc9b0d6938f8d66857e6c279e76e7b58e006e0e44",
  "icons-pwa/icon-180.png": "dd0ef838baf87ab2d1b05781803a8a8b9169a4c311ee3d1ab4556db3b7161b1a",
  "icons-pwa/icon-192.png": "cf1d0ea2d7ce39a36f0838c041c8c6fd37fbff60b06ad0c9bc900b05efc26d9a",
  "icons-pwa/icon-512.png": "794068368c0da969091b10010bcc2551fcb40630fa235fb6f4cbf8ead421cb09",
  "favicon-16x16.png": "b16ada899abe1fd426610549e672b69d84c21fc48c00fc07179ab0c3ba48b1af",
  "favicon-32x32.png": "d7b91ebad456fb03289b213c916d9dc9a7a16fc9e8b802324af65267c11ecdc8",
  "black-icon.svg": "d90535e2d1fb90f6b9cdf5f5f35b84ece1ba8cf8982454f470c4ba9d6cd9d46c",
  "white-icon.svg": "0293e38567850f656efa6c4f0140fa742b172d583d5d96ce5c186c529cf7c791",
};

describe("FreeBuild Mail branding", () => {
  it("names the PWA FreeBuild Mail", () => {
    const manifest = JSON.parse(read("apps/email/client/public/manifest.json")) as Record<string, string>;
    expect(manifest.name).toBe("FreeBuild Mail");
    for (const field of ["name", "short_name", "description"]) {
      expect(manifest[field], field).not.toMatch(ZERO_BRAND);
    }
  });

  it("does not ship the Zero images, and ships the FreeBuild monogram", () => {
    for (const [file, upstream] of Object.entries(UPSTREAM_IMAGE_HASHES)) {
      if (existsSync(join(PUBLIC, file))) expect(sha256(file), `${file} is still Zero's`).not.toBe(upstream);
    }
    expect(read("apps/email/client/public/assets/freebuild-mail.svg")).toContain("FB");
    for (const file of ["og.png", "favicon.ico", "apple-touch-icon.png", "icons-pwa/icon-512.png"]) {
      expect(existsSync(join(PUBLIC, file)), file).toBe(true);
    }
  });

  it("serves no Mail-0 logo files", () => {
    const names = [...readdirSync(PUBLIC), ...readdirSync(join(PUBLIC, "assets"))];
    expect(names.filter((name) => /^m0\b|mail0|^black-icon|^white-icon/i.test(name))).toEqual([]);
  });

  it("names FreeBuild Mail in the command palette and package description", () => {
    const messages = JSON.parse(read("apps/email/client/messages/en.json")) as {
      common: { commandPalette: { description: string } };
    };
    expect(messages.common.commandPalette.description).toBe("Quick navigation and actions for FreeBuild Mail");
    expect(read("apps/email/client/messages/en.json")).not.toMatch(/mail-?0|mail0/i);
    const pkg = JSON.parse(read("apps/email/package.json")) as { description: string };
    expect(pkg.description).not.toMatch(ZERO_BRAND);
  });

  it("shows no Zero text in the envelope illustration or the build config", () => {
    expect(read("apps/email/client/components/ui/envelop.tsx")).not.toContain("0 Email");
    expect(read("apps/email/client/wrangler.jsonc")).not.toMatch(/0\.email/);
  });

  it("credits Zero in NOTICE", () => {
    expect(read("NOTICE")).toContain("https://github.com/Mail-0/Zero");
  });
});
