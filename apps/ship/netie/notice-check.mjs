#!/usr/bin/env node
// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
//
// Apache-2.0 section 4(b) change notices for the FreeBuild fork.
//
// Compares every file under apps/ship against the pristine upstream Openship
// checkout at the same path. A file whose bytes differ was modified by Netie AI:
//   - comment-capable files must carry "Modified by Netie AI" in the file;
//   - comment-less files (JSON, images, lockfiles, patches, manifests, other)
//     are listed in apps/ship/NOTICE instead.
// Upstream files missing here are reported as deleted, grouped by unit.
//
// Usage (from apps/ship, or anywhere):
//   node netie/notice-check.mjs --check   exit 1 if a header is missing or misplaced, or NOTICE is stale
//   node netie/notice-check.mjs --write   insert missing headers, fix misplaced ones, regenerate NOTICE
//   node netie/notice-check.mjs --notice  regenerate NOTICE only
// Options: --upstream <dir> (or OPENSHIP_UPSTREAM_DIR), --verbose.

import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { basename, dirname, extname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const SHIP_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const NOTICE_PATH = join(SHIP_ROOT, "NOTICE");
const MARKER = "Modified by Netie AI";
const HEADER_TEXT = "Modified by Netie AI, 2026";
const UPSTREAM_COMMIT = "aba12c8dbee5cd92673645796ae68616a8fa30e1";

// Upstream units that were imported only in part (a few files restored on
// purpose). They count as pruned, so their missing files are not "deletions".
const PARTIAL_UNITS = new Set(["apps/web"]);

const args = process.argv.slice(2);
const mode = args.includes("--write")
  ? "write"
  : args.includes("--notice")
    ? "notice"
    : args.includes("--check")
      ? "check"
      : null;
const verbose = args.includes("--verbose");
const upIdx = args.indexOf("--upstream");
const UPSTREAM =
  upIdx !== -1 ? resolve(args[upIdx + 1] ?? "") : process.env.OPENSHIP_UPSTREAM_DIR ?? "";

if (!mode) {
  process.stderr.write("usage: notice-check.mjs --check | --write | --notice [--upstream <dir>] [--verbose]\n");
  process.exit(2);
}
if (!UPSTREAM || !existsSync(join(UPSTREAM, ".git"))) {
  process.stderr.write(
    "notice-check: need the pristine Openship git checkout at commit " +
      UPSTREAM_COMMIT +
      ".\nPass --upstream <dir> or set OPENSHIP_UPSTREAM_DIR.\n",
  );
  process.exit(2);
}

const upstreamHead = execFileSync("git", ["-C", UPSTREAM, "rev-parse", "HEAD"], { encoding: "utf8" }).trim();
if (upstreamHead !== UPSTREAM_COMMIT) {
  process.stderr.write(`notice-check: upstream checkout is at ${upstreamHead}, expected ${UPSTREAM_COMMIT}.\n`);
  process.exit(2);
}

const upstreamFiles = execFileSync("git", ["-C", UPSTREAM, "ls-files", "-z"], {
  encoding: "utf8",
  maxBuffer: 64 * 1024 * 1024,
})
  .split("\0")
  .filter(Boolean)
  .sort();

// ---------------------------------------------------------------- classify

const LOCKFILES = new Set(["package-lock.json", "bun.lock", "bun.lockb", "yarn.lock", "pnpm-lock.yaml"]);
const IMAGE_EXT = new Set([".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg", ".avif", ".bmp"]);

/** Returns the comment style key for a comment-capable file, or null. */
function commentStyle(path) {
  const name = basename(path);
  if (LOCKFILES.has(name)) return null;
  if (name === "Dockerfile" || name.startsWith("Dockerfile.") || name.endsWith(".Dockerfile")) return "dockerfile";
  switch (extname(name).toLowerCase()) {
    case ".ts":
    case ".tsx":
    case ".js":
    case ".mjs":
    case ".cjs":
      return "js";
    case ".css":
    case ".scss":
      return "css";
    case ".sh":
      return "sh";
    case ".py":
      return "py";
    case ".yml":
    case ".yaml":
    case ".toml":
      return "hash";
    case ".md":
      return "md";
    case ".mdx":
      return "mdx";
    case ".html":
      return "html";
    case ".hbs":
      return "hbs";
    default:
      return null;
  }
}

function commentlessKind(path) {
  const name = basename(path);
  const ext = extname(name).toLowerCase();
  if (LOCKFILES.has(name)) return "lockfile";
  if (ext === ".json") return "json";
  if (IMAGE_EXT.has(ext)) return "image";
  if (ext === ".patch") return "patch";
  if (ext === ".webmanifest") return "webmanifest";
  return "other";
}

function unitOf(path) {
  const parts = path.split("/");
  if (parts.length === 1) return ".";
  if ((parts[0] === "apps" || parts[0] === "packages") && parts.length > 2) return `${parts[0]}/${parts[1]}`;
  return parts[0];
}

// ---------------------------------------------------------------- headers

const DIRECTIVE_RE = /^\s*(['"])use (client|server|strict)\1;?\s*$/;
const LICENSE_RE = /copyright|licen[cs]e|spdx/i;

function headerLine(style) {
  switch (style) {
    case "js":
      return `// ${HEADER_TEXT}`;
    case "css":
      return `/* ${HEADER_TEXT} */`;
    case "md":
    case "html":
      return `<!-- ${HEADER_TEXT} -->`;
    case "mdx":
      return `{/* ${HEADER_TEXT} */}`;
    case "hbs":
      return `{{!-- ${HEADER_TEXT} --}}`;
    default:
      return `# ${HEADER_TEXT}`;
  }
}

/** End index (exclusive) of a leading comment block starting at `i`, or `i` if none. */
function jsCommentBlockEnd(lines, i) {
  const first = (lines[i] ?? "").trim();
  if (first.startsWith("//")) {
    let j = i;
    while (j < lines.length && lines[j].trim().startsWith("//")) j++;
    return j;
  }
  if (first.startsWith("/*")) {
    let j = i;
    while (j < lines.length && !lines[j].includes("*/")) j++;
    return Math.min(j + 1, lines.length);
  }
  return i;
}

function hashCommentBlockEnd(lines, i) {
  let j = i;
  while (j < lines.length && lines[j].trim().startsWith("#")) j++;
  return j;
}

function frontmatterEnd(lines) {
  if (lines[0]?.trim() !== "---") return 0;
  for (let j = 1; j < lines.length; j++) if (lines[j].trim() === "---") return j + 1;
  return 0;
}

/** Index of the "use ..." directive line that ends the directive prologue, or -1. */
function lastDirectiveIndex(lines) {
  let i = lines[0]?.startsWith("#!") ? 1 : 0;
  let last = -1;
  while (i < lines.length) {
    const t = lines[i].trim();
    if (t === "") {
      i++;
      continue;
    }
    if (t.startsWith("//") || t.startsWith("/*")) {
      i = jsCommentBlockEnd(lines, i);
      continue;
    }
    if (DIRECTIVE_RE.test(lines[i])) {
      last = i;
      i++;
      continue;
    }
    break;
  }
  return last;
}

/** Line index where the header goes. */
function insertIndex(style, lines) {
  let i = 0;
  if (style === "js") {
    if (lines[0]?.startsWith("#!")) i = 1;
    const end = jsCommentBlockEnd(lines, i);
    if (end > i && LICENSE_RE.test(lines.slice(i, end).join("\n"))) i = end;
    const d = lastDirectiveIndex(lines);
    if (d >= i) i = d + 1;
    return i;
  }
  if (style === "css") {
    const end = jsCommentBlockEnd(lines, 0);
    if (end > 0 && LICENSE_RE.test(lines.slice(0, end).join("\n"))) i = end;
    // @charset must stay the first thing in a stylesheet.
    if (/^@charset\s/i.test(lines[i] ?? "")) i++;
    return i;
  }
  if (style === "md" || style === "mdx") return frontmatterEnd(lines);
  if (style === "html") {
    if (/^\s*<!doctype/i.test(lines[0] ?? "")) return 1;
    return 0;
  }
  if (style === "hbs") return 0;
  // "#" styles: sh, py, dockerfile, yml/yaml/toml.
  if (lines[0]?.startsWith("#!")) i = 1;
  if (style === "py") {
    // PEP 263 coding line must stay in the first two lines.
    if (/^#.*coding[:=]/.test(lines[i] ?? "")) i++;
  }
  if (style === "dockerfile") {
    // Parser directives (# syntax=, # escape=, # check=) must stay first.
    while (i < lines.length && /^#\s*[a-z]+\s*=/i.test(lines[i])) i++;
  }
  const end = hashCommentBlockEnd(lines, i);
  if (end > i && LICENSE_RE.test(lines.slice(i, end).join("\n"))) i = end;
  return i;
}

/** True when a "Modified by Netie AI" line sits above the directive prologue of a JS/TS file. */
function headerBeforeDirective(text) {
  const lines = text.split(/\r?\n/);
  const d = lastDirectiveIndex(lines);
  if (d === -1) return false;
  return lines.slice(0, d).some((l) => l.includes(MARKER));
}

/**
 * Moves single-line marker comments that sit above the directive prologue to
 * just below it. Returns null when the marker is part of a multi-line comment
 * (left for a human).
 */
function moveHeaderBelowDirective(text) {
  const eol = text.includes("\r\n") ? "\r\n" : "\n";
  const lines = text.split(/\r?\n/);
  const d = lastDirectiveIndex(lines);
  const moved = [];
  const kept = [];
  for (let i = 0; i < lines.length; i++) {
    if (i < d && lines[i].includes(MARKER)) {
      const t = lines[i].trim();
      const single = t.startsWith("//") || (t.startsWith("/*") && t.endsWith("*/"));
      if (!single) return null;
      moved.push(lines[i]);
    } else {
      kept.push(lines[i]);
    }
  }
  const newD = lastDirectiveIndex(kept);
  kept.splice(newD + 1, 0, ...moved);
  return kept.join(eol);
}

function insertHeader(style, text) {
  const eol = text.includes("\r\n") ? "\r\n" : "\n";
  const lines = text.split(/\r?\n/);
  const at = insertIndex(style, lines);
  lines.splice(at, 0, headerLine(style));
  return lines.join(eol);
}

// ---------------------------------------------------------------- scan

function sameBytes(a, b) {
  return a.length === b.length && a.equals(b);
}

const modifiedCommentable = [];
const modifiedCommentless = [];
const missingHeader = [];
const misplaced = [];
const deleted = [];
let unchanged = 0;

for (const rel of upstreamFiles) {
  const here = join(SHIP_ROOT, rel);
  if (!existsSync(here) || !statSync(here).isFile()) {
    deleted.push(rel);
    continue;
  }
  const mine = readFileSync(here);
  const theirs = readFileSync(join(UPSTREAM, rel));
  if (sameBytes(mine, theirs)) {
    unchanged++;
    continue;
  }
  const style = commentStyle(rel);
  const text = mine.toString("utf8");
  if (!style) {
    modifiedCommentless.push({ path: rel, kind: commentlessKind(rel), marked: text.includes(MARKER) });
    continue;
  }
  modifiedCommentable.push(rel);
  if (!text.includes(MARKER)) missingHeader.push({ path: rel, style });
  else if (style === "js" && headerBeforeDirective(text)) misplaced.push(rel);
}

// Units: a unit (apps/X, packages/X, or a top-level dir) that does not exist
// here, or is listed in PARTIAL_UNITS, was pruned as a whole.
const keptDeleted = new Map();
const prunedDeleted = new Map();
for (const rel of deleted) {
  const unit = unitOf(rel);
  const pruned = unit !== "." && (PARTIAL_UNITS.has(unit) || !existsSync(join(SHIP_ROOT, unit)));
  const bucket = pruned ? prunedDeleted : keptDeleted;
  if (!bucket.has(unit)) bucket.set(unit, []);
  bucket.get(unit).push(rel);
}

// ---------------------------------------------------------------- NOTICE

const files = (n) => `${n} file${n === 1 ? "" : "s"}`;

function renderNotice() {
  const out = [];
  const p = (s = "") => out.push(s);
  p("FreeBuild");
  p("Copyright (c) 2026 Netie AI");
  p();
  p("FreeBuild, Copyright (c) 2026 Netie AI. This product includes software");
  p("developed by Oblien as Openship (https://github.com/oblien/openship),");
  p("licensed under the Apache License 2.0, imported at commit");
  p(`${UPSTREAM_COMMIT}. Netie AI modified it; modified`);
  p("source files carry a 'Modified by Netie AI, 2026' notice.");
  p();
  p("The full license text is in LICENSE. Root THIRD_PARTY_NOTICES.md (in the");
  p("OpenVault repository) records the import and what was left out.");
  p();
  p("This file is generated by `node netie/notice-check.mjs --notice`. Do not");
  p("edit it by hand. Paths are relative to apps/ship.");
  p();
  p("Modified files without a comment notice");
  p("---------------------------------------");
  p();
  p("These files differ from upstream and have no comment syntax (JSON, images,");
  p("lockfiles, patches, web manifests and other formats), so Netie AI's");
  p("modification notice for them is this list.");
  p();
  const listed = modifiedCommentless.filter((f) => !f.marked);
  if (listed.length === 0) p("  (none)");
  for (const f of listed) p(`  ${f.path}`);
  p();
  p("Upstream files removed");
  p("----------------------");
  p();
  p("Units pruned as a whole (not shipped; see the pruned list in the root");
  p("THIRD_PARTY_NOTICES.md). File counts at the imported commit:");
  p();
  const pruned = [...prunedDeleted.keys()].sort();
  if (pruned.length === 0) p("  (none)");
  for (const u of pruned) {
    const note = PARTIAL_UNITS.has(u) ? " (a few files restored on purpose)" : "";
    p(`  ${u}/: ${files(prunedDeleted.get(u).length)}${note}`);
  }
  p();
  p("Files removed from kept units, by unit:");
  p();
  const kept = [...keptDeleted.keys()].sort();
  if (kept.length === 0) p("  (none)");
  for (const u of kept) p(`  ${u === "." ? "(root)" : `${u}/`}: ${files(keptDeleted.get(u).length)}`);
  for (const u of kept) {
    p();
    p(`  ${u === "." ? "(root)" : `${u}/`}`);
    for (const f of keptDeleted.get(u)) p(`    ${f}`);
  }
  p();
  return out.join("\n");
}

// ---------------------------------------------------------------- act

let wrote = 0;
let moved = 0;
const unmovable = [];
if (mode === "write") {
  for (const { path, style } of missingHeader) {
    const abs = join(SHIP_ROOT, path);
    writeFileSync(abs, insertHeader(style, readFileSync(abs, "utf8")));
    wrote++;
    if (verbose) process.stdout.write(`header: ${path}\n`);
  }
  for (const path of misplaced) {
    const abs = join(SHIP_ROOT, path);
    const next = moveHeaderBelowDirective(readFileSync(abs, "utf8"));
    if (next === null) {
      unmovable.push(path);
      continue;
    }
    writeFileSync(abs, next);
    moved++;
    if (verbose) process.stdout.write(`moved below directive: ${path}\n`);
  }
}

const notice = renderNotice();
const currentNotice = existsSync(NOTICE_PATH) ? readFileSync(NOTICE_PATH, "utf8") : "";
const noticeStale = currentNotice !== notice;
if ((mode === "write" || mode === "notice") && noticeStale) writeFileSync(NOTICE_PATH, notice);

const count = (m) => [...m.values()].reduce((n, l) => n + l.length, 0);
const kinds = {};
for (const f of modifiedCommentless) kinds[f.kind] = (kinds[f.kind] ?? 0) + 1;

const summary = [
  `upstream files:                 ${upstreamFiles.length}`,
  `unchanged:                      ${unchanged}`,
  `modified, comment-capable:      ${modifiedCommentable.length}`,
  `  missing header:               ${mode === "write" ? missingHeader.length - wrote : missingHeader.length}${mode === "write" ? ` (inserted ${wrote})` : ""}`,
  `  header above directive:       ${mode === "write" ? unmovable.length : misplaced.length}${mode === "write" ? ` (moved ${moved})` : ""}`,
  `modified, comment-less:         ${modifiedCommentless.length} ${JSON.stringify(kinds)}`,
  `deleted, in kept units:         ${count(keptDeleted)}`,
  `deleted, in pruned units:       ${count(prunedDeleted)}`,
  `NOTICE:                         ${noticeStale ? (mode === "check" ? "STALE" : "regenerated") : "up to date"}`,
];
process.stdout.write(summary.join("\n") + "\n");

if (mode === "check") {
  for (const { path } of missingHeader) process.stdout.write(`missing header: ${path}\n`);
  for (const path of misplaced) process.stdout.write(`header above directive: ${path}\n`);
  if (noticeStale) process.stdout.write("NOTICE is stale: run `node netie/notice-check.mjs --notice`\n");
  if (missingHeader.length || misplaced.length || noticeStale) process.exit(1);
} else if (mode === "write" && unmovable.length) {
  for (const path of unmovable) process.stdout.write(`header above directive, multi-line, fix by hand: ${path}\n`);
  process.exit(1);
}
