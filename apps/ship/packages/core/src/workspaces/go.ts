import type { WorkspaceDetector } from "./types";

/**
 * Go workspaces - `go.work` declares one or more `use` directives:
 *
 *     go 1.22
 *
 *     use ./hello
 *     use (
 *         ./hello
 *         ./helloutil
 *     )
 *
 * We collect every `use`-referenced path (both single-line and block form),
 * preserving leading `./` so the downstream matcher gets the literal path
 * the user wrote.
 */
function parseGoWork(content: string): string[] {
  const paths: string[] = [];

  // Block form: use ( ... ). May span multiple lines. Capture the body and
  // record the source range so the single-line scan below skips these regions.
  const blockPattern = /(?:^|\n)\s*use\s*\(\s*([\s\S]*?)\)/g;
  const consumed: Array<[number, number]> = [];
  let blockMatch: RegExpExecArray | null;
  while ((blockMatch = blockPattern.exec(content)) !== null) {
    consumed.push([blockMatch.index, blockMatch.index + blockMatch[0].length]);
    for (const rawLine of blockMatch[1].split("\n")) {
      const line = rawLine.replace(/\/\/.*$/, "").trim();
      if (line) paths.push(line);
    }
  }

  // Single-line form: use ./path - but only outside the block ranges so we
  // don't pick up the `use (` opener as a stray "use" + "(" pair.
  const isInsideBlock = (offset: number) =>
    consumed.some(([start, end]) => offset >= start && offset < end);

  const singleLinePattern = /(?:^|\n)([^\n]+)/g;
  let lineMatch: RegExpExecArray | null;
  while ((lineMatch = singleLinePattern.exec(content)) !== null) {
    if (isInsideBlock(lineMatch.index)) continue;
    const line = lineMatch[1].replace(/\/\/.*$/, "").trim();
    const match = line.match(/^use\s+([^\s(]+)\s*$/);
    if (match) paths.push(match[1]);
  }

  // Dedupe while preserving order.
  const seen = new Set<string>();
  return paths.filter((p) => {
    if (seen.has(p)) return false;
    seen.add(p);
    return true;
  });
}

export const goWorkspaceDetector: WorkspaceDetector = {
  id: "go-work",
  label: "Go workspace",
  manifestFiles: ["go.work"],
  // No packageManager - `go build` resolves go.work automatically.
  parseSubProjects: parseGoWork,
};
