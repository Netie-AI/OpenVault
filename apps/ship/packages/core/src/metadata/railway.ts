import type { DeploymentMetadata, MetadataParser } from "./types";
import { extractCdTargets } from "./vercel";
import { splitLines, trimmed } from "./text";

/**
 * Normalized view of a `railway.toml` / `railway.json` — the fields that shape
 * how a project builds and runs. Railway keeps env vars, replicas, health
 * checks, and cron OUT of this file (those are per-service/template settings set
 * in the platform), so we only carry the build/start commands + the builder.
 */
interface RailwayConfig {
  /** NIXPACKS (default) | RAILPACK | DOCKERFILE */
  builder?: string;
  buildCommand?: string;
  startCommand?: string;
}

/**
 * Read a scalar from the right-hand side of a `key = value` TOML line. Honors
 * quotes so a `#` or `=` inside a quoted command isn't mistaken for a comment or
 * assignment; a bare value has any inline `# comment` stripped. Basic
 * (double-quoted) strings decode the standard TOML escapes; literal
 * (single-quoted) strings are taken verbatim. Multi-line (`"""`/`'''`) strings
 * are handled by the caller, not here.
 */
function tomlScalar(rest: string): string | undefined {
  const s = rest.trim();
  const quote = s[0];
  if (quote === '"' || quote === "'") {
    let out = "";
    for (let i = 1; i < s.length; i++) {
      const ch = s[i];
      if (quote === '"' && ch === "\\" && i + 1 < s.length) {
        const next = s[++i];
        switch (next) {
          case "t": out += "\t"; break;
          case "n": out += "\n"; break;
          case "r": out += "\r"; break;
          case "b": out += "\b"; break;
          case "f": out += "\f"; break;
          case '"': out += '"'; break;
          case "\\": out += "\\"; break;
          case "/": out += "/"; break;
          case "u":
          case "U": {
            const width = next === "u" ? 4 : 8;
            const hex = s.slice(i + 1, i + 1 + width);
            if (hex.length === width && /^[0-9a-fA-F]+$/.test(hex)) {
              out += String.fromCodePoint(parseInt(hex, 16));
              i += width;
            } else {
              out += next; // malformed escape → keep the char literally
            }
            break;
          }
          default:
            out += next;
        }
        continue;
      }
      if (ch === quote) return out;
      out += ch;
    }
    return undefined; // unterminated string → treat as absent
  }
  const bare = s.split("#")[0].trim();
  return bare.length > 0 ? bare : undefined;
}

/**
 * Parse `railway.toml` WITHOUT a TOML dependency — mirrors the hand-rolled
 * `render.yaml` reader. Reads the scalar `buildCommand`/`builder` keys under
 * `[build]` and `startCommand` under `[deploy]`, in either the table form
 * (`[build]` + `buildCommand = …`) or the dotted form (`build.buildCommand = …`).
 * Sub-tables (`nixpacksPlan`) and arrays (`watchPatterns`) are ignored.
 */
function parseRailwayToml(raw: string): RailwayConfig {
  const cfg: RailwayConfig = {};
  let section = "";
  const lines = splitLines(raw);

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // [table] — allow a trailing inline `# comment` after the closing bracket.
    const header = line.match(/^\s*\[([^\]]+)\]\s*(?:#.*)?$/);
    if (header) {
      section = header[1].trim().toLowerCase();
      continue;
    }

    // key = value — the key may be dotted (`build.buildCommand`).
    const kv = line.match(/^\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*=\s*(.+)$/);
    if (!kv) continue;
    const [, rawKey, rest] = kv;

    // Resolve table.field from either the active [section] or a dotted key.
    const dot = rawKey.lastIndexOf(".");
    const table = dot >= 0 ? rawKey.slice(0, dot).toLowerCase() : section;
    const field = dot >= 0 ? rawKey.slice(dot + 1) : rawKey;

    // Multi-line (""" / ''') strings: capture the inner text when it opens and
    // closes on the same line; otherwise consume through the closing delimiter
    // so the body lines can't be misread as keys (a body line that looks like
    // `builder = "DOCKERFILE"` must NOT become a real key).
    let value: string | undefined;
    const triple = rest.match(/^("""|''')/);
    if (triple) {
      const delim = triple[1];
      const afterOpen = rest.slice(3);
      const close = afterOpen.indexOf(delim);
      if (close >= 0) {
        value = afterOpen.slice(0, close).trim() || undefined;
      } else {
        while (++i < lines.length && !lines[i].includes(delim)) {
          /* skip the multi-line body */
        }
        value = undefined;
      }
    } else {
      value = tomlScalar(rest);
    }
    if (!value) continue;

    if (table === "build") {
      if (field === "buildCommand") cfg.buildCommand ??= value;
      else if (field === "builder") cfg.builder ??= value;
    } else if (table === "deploy") {
      if (field === "startCommand") cfg.startCommand ??= value;
    }
  }
  return cfg;
}

/** Parse `railway.json`: `{ build: { builder, buildCommand }, deploy: { startCommand } }`. */
function parseRailwayJson(raw: string): RailwayConfig | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  // `JSON.parse("null")` succeeds → guard before property access, or a stray
  // `null`/primitive file would throw and crash the metadata pipeline.
  if (typeof parsed !== "object" || parsed === null) return null;
  const obj = parsed as Record<string, unknown>;
  const build = (obj.build ?? {}) as Record<string, unknown>;
  const deploy = (obj.deploy ?? {}) as Record<string, unknown>;
  return {
    builder: trimmed(build.builder),
    buildCommand: trimmed(build.buildCommand),
    startCommand: trimmed(deploy.startCommand),
  };
}

/** A RailwayConfig → normalized metadata, or null when it declares no signal. */
function toMetadata(cfg: RailwayConfig): DeploymentMetadata | null {
  const buildCommand = trimmed(cfg.buildCommand);
  const startCommand = trimmed(cfg.startCommand);
  // A DOCKERFILE builder means "build from the Dockerfile" → the docker stack.
  // NIXPACKS/RAILPACK auto-detect, so we leave detection to openship.
  const framework = cfg.builder?.toUpperCase() === "DOCKERFILE" ? "docker" : undefined;
  if (!buildCommand && !startCommand && !framework) return null;

  const metadata: DeploymentMetadata = { source: "railway" };
  if (buildCommand) metadata.buildCommand = buildCommand;
  if (startCommand) metadata.startCommand = startCommand;
  if (framework) metadata.framework = framework;
  // A build command that `cd`s elsewhere describes another directory's build,
  // so — like a root vercel.json — don't apply this file to the dir it sits in.
  if (extractCdTargets(buildCommand).length > 0) metadata.nonLocal = true;
  return metadata;
}

/**
 * Parse Railway's project config (`railway.toml` or `railway.json`) into
 * normalized build/run hints, so a repo already configured for Railway deploys
 * the same way here. The build/start commands are explicit user intent, so
 * they're authoritative (they override heuristic detection, like `vercel.json`);
 * a `DOCKERFILE` builder maps to the docker stack. `railway.toml` wins when it
 * declares a signal; a content-free toml falls back to `railway.json`.
 */
export const railwayMetadataParser: MetadataParser = {
  source: "railway",
  files: ["railway.toml", "railway.json"],
  parse(fileContents) {
    const tomlRaw = fileContents["railway.toml"];
    const jsonRaw = fileContents["railway.json"];
    const fromToml = tomlRaw ? toMetadata(parseRailwayToml(tomlRaw)) : null;
    if (fromToml) return fromToml;
    const jsonCfg = jsonRaw ? parseRailwayJson(jsonRaw) : null;
    return jsonCfg ? toMetadata(jsonCfg) : null;
  },
};
