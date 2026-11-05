// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
//
// Guards the user-visible strings that are easy to miss in a rename: input
// placeholders, default values, download filenames, toast and alert text,
// aria-label and title attributes, meta descriptions, and the English
// dictionary. Upstream product names must not appear in them (DR-0018 hard
// line 4). Internal identifiers (storage keys, env var names, import paths,
// API field names, code comments) are not scanned and may keep upstream names.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const SHIP_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../../../..");

const UPSTREAM_NAME = /openship|oblien|opshcloud|opsh\.io/i;

/** Roots that ship user-facing strings. Engine samples and docs are not scanned. */
const SCAN_ROOTS = [
  "apps/dashboard/src",
  "apps/dashboard/public",
  "apps/email/client/app",
  "apps/email/client/components",
  "apps/email/client/hooks",
  "apps/email/client/lib",
  "apps/email/client/messages",
  "apps/email/client/providers",
  "apps/email/client/config",
  "apps/email/client/utils",
  "apps/email/client/store",
  "apps/email/server/src",
];

const SOURCE_EXT = /\.(tsx?|jsx?|mjs|html)$/;
const SKIP_DIR = new Set(["node_modules", ".next", "dist", "build", ".turbo"]);

function isTestFile(path: string): boolean {
  return /\.(test|spec)\.[tj]sx?$/.test(path) || /[\\/](__tests__|test)[\\/]/.test(path);
}

function walk(dir: string, out: string[] = []): string[] {
  let entries: string[];
  try {
    entries = readdirSync(dir);
  } catch {
    return out;
  }
  for (const name of entries) {
    if (SKIP_DIR.has(name)) continue;
    const full = join(dir, name);
    if (statSync(full).isDirectory()) walk(full, out);
    else out.push(full);
  }
  return out;
}

const sourceFiles = SCAN_ROOTS.flatMap((root) => walk(join(SHIP_ROOT, root))).filter(
  (file) => SOURCE_EXT.test(file) && !isTestFile(file),
);

interface Hit {
  file: string;
  line: number;
  kind: string;
  text: string;
}

/** A string literal body: no quote of the same kind inside, may span a template. */
const LITERAL = String.raw`(["'\`])((?:(?!\1)[^\\\n]|\\.)*?)\1`;

const PATTERNS: { kind: string; re: RegExp }[] = [
  // placeholder="...", aria-label="...", title="...", alt="...", content="..." and
  // the same wrapped in braces: placeholder={"..."}.
  {
    kind: "attribute",
    re: new RegExp(
      String.raw`\b(?:placeholder|aria-label|aria-description|aria-placeholder|aria-roledescription|title|alt|defaultValue|content)\s*=\s*\{?\s*` +
        LITERAL,
      "g",
    ),
  },
  // Object properties that become one of the attributes above, a meta tag or
  // visible copy: Next `metadata`, option lists, empty states, tooltips.
  {
    kind: "property",
    re: new RegExp(
      String.raw`\b(?:placeholder|ariaLabel|defaultValue|siteTitle|siteDescription|applicationName|creator|title|description|label|heading|subtitle|tooltip|hint|helpText|message|summary|text)\s*:\s*` +
        LITERAL,
      "g",
    ),
  },
  // JSX text between tags: <p>Connect to Openship</p>.
  {
    kind: "jsx text",
    re: />([^<>{}\n]*)</g,
  },
  // Download filenames.
  {
    kind: "download",
    re: new RegExp(String.raw`\.download\s*=\s*` + LITERAL, "g"),
  },
  {
    kind: "download",
    re: new RegExp(String.raw`(?<![.\w])download\s*=\s*\{?\s*` + LITERAL, "g"),
  },
  // const filename = `...` feeding a download.
  {
    kind: "download",
    re: new RegExp(String.raw`\bconst\s+filename\s*=\s*` + LITERAL, "g"),
  },
  // Toast, alert and confirm text.
  {
    kind: "dialog text",
    re: new RegExp(
      String.raw`\b(?:showToast|toast(?:\.\w+)?|alert|confirm|prompt)\(\s*` + LITERAL,
      "g",
    ),
  },
];

function lineOf(text: string, index: number): number {
  let line = 1;
  for (let i = 0; i < index; i++) if (text.charCodeAt(i) === 10) line++;
  return line;
}

function scan(file: string, text: string): Hit[] {
  const hits: Hit[] = [];
  for (const { kind, re } of PATTERNS) {
    re.lastIndex = 0;
    for (let match = re.exec(text); match; match = re.exec(text)) {
      // LITERAL patterns capture (quote, body); the JSX text pattern captures the body alone.
      const body = match[2] ?? match[1] ?? "";
      if (UPSTREAM_NAME.test(body)) {
        hits.push({
          file: relative(SHIP_ROOT, file).replaceAll("\\", "/"),
          line: lineOf(text, match.index),
          kind,
          text: body,
        });
      }
    }
  }
  return hits;
}

describe("shipped strings carry no upstream product names", () => {
  it("finds the source files it is supposed to scan", () => {
    expect(sourceFiles.length).toBeGreaterThan(300);
    expect(sourceFiles.some((f) => f.endsWith("CreateDestinationModal.tsx"))).toBe(true);
    expect(sourceFiles.some((f) => f.endsWith("ExportPanel.tsx"))).toBe(true);
  });

  it("the scanner recognises each class it guards", () => {
    const sample = [
      '<input placeholder="/backups/openship" />',
      '<button aria-label="Open Oblien">x</button>',
      '<div title={"openship"} />',
      'anchor.download = "openship-export.json";',
      "const filename = `openship-${label}.json`;",
      'showToast("Saved to Openship", "success");',
      "window.confirm(`Reset openship?`);",
      'const meta = { siteTitle: "OpenShip Mail" };',
      'export const metadata = { title: "Openship", description: "Deploy with Openship" };',
      "<p>Connect to Openship</p>",
    ].join("\n");
    const kinds = scan("sample.tsx", sample).map((hit) => hit.kind);
    expect(kinds.sort()).toEqual(
      [
        "attribute",
        "attribute",
        "attribute",
        "dialog text",
        "dialog text",
        "download",
        "download",
        "jsx text",
        "property",
        "property",
        "property",
      ].sort(),
    );
  });

  it("source files: placeholders, defaults, downloads, toasts, aria-label, title and meta", () => {
    const hits = sourceFiles.flatMap((file) => scan(file, readFileSync(file, "utf8")));
    expect(hits.map((h) => `${h.file}:${h.line} [${h.kind}] ${h.text}`)).toEqual([]);
  });

  // Internal identifiers that are allowed to read as upstream names inside a
  // sentence: an on-disk path, an env var name, the config file a repo ships,
  // and the legacy webhook header that is documented next to its FreeBuild twin.
  const ALLOWED = [
    /\/root\/\.openship\/[\w./-]+/g,
    /\bOPENSHIP_[A-Z0-9_]+/g,
    /\bopenship\.json\b/g,
    /\bX-Openship-Signature-256\b/g,
  ];

  /** Every string in every JSON file of a dictionary directory that reads as an upstream name. */
  function dictionaryOffenders(relDir: string): { files: number; offenders: string[] } {
    const dir = join(SHIP_ROOT, relDir);
    const offenders: string[] = [];
    const visit = (value: unknown, path: string) => {
      if (typeof value === "string") {
        const stripped = ALLOWED.reduce((acc, re) => acc.replace(re, ""), value);
        if (UPSTREAM_NAME.test(stripped)) offenders.push(`${path}: ${value}`);
      } else if (Array.isArray(value)) {
        value.forEach((v, i) => visit(v, `${path}[${i}]`));
      } else if (value && typeof value === "object") {
        for (const [key, v] of Object.entries(value)) visit(v, path ? `${path}.${key}` : key);
      }
    };
    const names = readdirSync(dir).filter((n) => n.endsWith(".json"));
    for (const name of names) visit(JSON.parse(readFileSync(join(dir, name), "utf8")), `${relDir}/${name}`);
    return { files: names.length, offenders };
  }

  it("the English dictionary", () => {
    const { files, offenders } = dictionaryOffenders("apps/dashboard/src/i18n/locales/en");
    expect(files).toBeGreaterThan(20);
    expect(offenders).toEqual([]);
  });

  it("the webmail message catalogue", () => {
    const { files, offenders } = dictionaryOffenders("apps/email/client/messages");
    expect(files).toBeGreaterThan(0);
    expect(offenders).toEqual([]);
  });
});
