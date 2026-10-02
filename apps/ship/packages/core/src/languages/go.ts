import type { LanguageDetector } from "./types";

/**
 * Go - `go.mod` declares modules in two forms:
 *
 *     require github.com/foo/bar v1.2.3
 *
 *     require (
 *         github.com/foo/bar v1.2.3
 *         github.com/baz/qux v4.5.6 // indirect
 *     )
 *
 * For each module we also write a `/vN`-stripped alias (e.g. `github.com/foo/bar/v2`
 * → `github.com/foo/bar`) so downstream framework detection matching by import path
 * doesn't have to know the major-version suffix.
 */
function parseGoMod(content: string): Record<string, string> {
  const deps: Record<string, string> = {};

  // Block form
  for (const block of content.matchAll(/require\s*\(([\s\S]*?)\)/g)) {
    for (const line of block[1].split("\n")) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("//")) continue;
      const parts = trimmed.split(/\s+/);
      if (parts.length >= 2) {
        deps[parts[0]] = parts[1];
        const base = parts[0].replace(/\/v\d+$/, "");
        if (base !== parts[0]) deps[base] = parts[1];
      }
    }
  }

  // Single-line form
  for (const m of content.matchAll(/^require\s+([\S]+)\s+([\S]+)/gm)) {
    deps[m[1]] = m[2];
    const base = m[1].replace(/\/v\d+$/, "");
    if (base !== m[1]) deps[base] = m[2];
  }

  return deps;
}

export const goLanguageDetector: LanguageDetector = {
  id: "go",
  label: "Go",
  manifestFiles: ["go.mod"],
  parseManifest: (_filename, content) => parseGoMod(content),
};
