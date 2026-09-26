import type { WorkspaceDetector } from "./types";

/**
 * .NET solutions - a `*.sln` file at the repo root references every project:
 *
 *     Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "Api", "src\Api\Api.csproj", "{…}"
 *     Project("{2150E333-8FDC-42A3-9474-1A3956D46DE8}") = "tests", "tests", "{…}"  # solution folder
 *
 * We extract the SECOND quoted string from each `Project(...)` line, take its
 * directory, and emit that as a sub-project path. Solution-folder entries
 * (whose second quoted value is just a name, not a path with a project file
 * extension) are filtered out.
 */
function parseSolution(content: string): string[] {
  const paths: string[] = [];
  const projectLine = /^Project\([^)]*\)\s*=\s*"[^"]*",\s*"([^"]+)"\s*,\s*"[^"]*"/gmi;

  let match: RegExpExecArray | null;
  while ((match = projectLine.exec(content)) !== null) {
    const projectFilePath = match[1].trim();
    // Skip solution folders (no project file extension).
    if (!/\.(csproj|fsproj|vbproj|sqlproj|vcxproj)$/i.test(projectFilePath)) {
      continue;
    }
    // sln paths use Windows-style backslashes - normalize.
    const normalized = projectFilePath.replace(/\\+/g, "/");
    const lastSlash = normalized.lastIndexOf("/");
    if (lastSlash === -1) {
      // Project at the solution root - emit "." which the matcher treats as the root.
      paths.push(".");
    } else {
      paths.push(normalized.slice(0, lastSlash));
    }
  }

  // Dedupe.
  const seen = new Set<string>();
  return paths.filter((p) => {
    if (seen.has(p)) return false;
    seen.add(p);
    return true;
  });
}

export const dotnetSolutionDetector: WorkspaceDetector = {
  id: "dotnet-sln",
  label: ".NET solution",
  manifestFiles: [/\.sln$/i],
  packageManager: "dotnet",
  parseSubProjects: parseSolution,
};
