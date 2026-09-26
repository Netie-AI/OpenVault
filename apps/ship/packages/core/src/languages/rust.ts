import type { LanguageDetector } from "./types";

/**
 * Rust - `Cargo.toml` dependencies live in any of:
 *
 *     [dependencies]
 *     [dev-dependencies]
 *     [build-dependencies]
 *     [workspace.dependencies]
 *
 * Section bodies terminate at `\n[` rather than just `[` so inline-table
 * values like `tokio = { features = ["full"] }` don't truncate the section.
 *
 * (This parses Cargo deps. The separate Cargo *workspace* parser lives under
 * `workspaces/cargo.ts` - they read the same file but answer different questions:
 * "which crates does this project depend on?" vs "which sub-projects does this
 * workspace contain?")
 */
function parseCargoToml(content: string): Record<string, string> {
  const deps: Record<string, string> = {};
  const sections = content.matchAll(
    /\[(?:workspace\.)?(?:dev-|build-)?dependencies\]([\s\S]*?)(?=\n\[|$)/g,
  );
  for (const section of sections) {
    for (const line of section[1].split("\n")) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const m = trimmed.match(/^([A-Za-z0-9_-]+)\s*=/);
      if (m) deps[m[1]] = "*";
    }
  }
  return deps;
}

export const rustLanguageDetector: LanguageDetector = {
  id: "rust",
  label: "Rust",
  manifestFiles: ["cargo.toml"],
  parseManifest: (_filename, content) => parseCargoToml(content),
};
