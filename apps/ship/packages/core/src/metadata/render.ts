import type { DeploymentMetadata, MetadataParser } from "./types";
import { splitLines } from "./text";

/** Unquote a scalar YAML value ("npm start" / 'npm start' / npm start). */
function unquote(value: string): string {
  const trimmed = value.trim();
  const m = trimmed.match(/^(['"])(.*)\1$/);
  return m ? m[2] : trimmed;
}

/**
 * Parse `render.yaml` build/run hints WITHOUT a YAML dependency - mirrors the
 * hand-rolled `parsePnpmWorkspaceYaml` in `workspaces/node.ts`.
 *
 * Deliberately shallow: takes the FIRST `startCommand:` / `buildCommand:` in the
 * file (the primary web service in the common single-service case) plus any
 * `envVars` entries that carry a literal `value:` (secrets use `sync: false` and
 * are skipped). These are FILL-ONLY hints - they never override what the stack
 * detector already resolved from package.json.
 */
export const renderMetadataParser: MetadataParser = {
  source: "render",
  files: ["render.yaml"],
  parse(fileContents) {
    const raw = fileContents["render.yaml"];
    if (!raw) return null;

    const lines = splitLines(raw);

    let startCommand: string | undefined;
    let buildCommand: string | undefined;
    const env: Record<string, string> = {};

    let pendingKey: string | undefined;
    for (const line of lines) {
      const startMatch = line.match(/^\s*startCommand:\s*(.+)$/);
      if (startMatch && !startCommand) startCommand = unquote(startMatch[1]);

      const buildMatch = line.match(/^\s*buildCommand:\s*(.+)$/);
      if (buildMatch && !buildCommand) buildCommand = unquote(buildMatch[1]);

      // envVars entries: `- key: FOO` on one line, `value: bar` on a later one.
      const keyMatch = line.match(/^\s*-?\s*key:\s*(.+)$/);
      if (keyMatch) {
        pendingKey = unquote(keyMatch[1]);
        continue;
      }
      const valueMatch = line.match(/^\s*value:\s*(.+)$/);
      if (valueMatch && pendingKey) {
        env[pendingKey] = unquote(valueMatch[1]);
        pendingKey = undefined;
      }
    }

    const metadata: DeploymentMetadata = { source: "render", fillOnly: true };
    // render.yaml's `buildCommand` conflates install+build for many stacks, so we
    // only surface it when it isn't a bare install - otherwise it's noise.
    if (startCommand) metadata.startCommand = startCommand;
    if (buildCommand && !/^(npm|yarn|pnpm|bun)\s+(install|i|ci)\b/.test(buildCommand)) {
      metadata.buildCommand = buildCommand;
    }
    if (Object.keys(env).length > 0) metadata.env = env;

    const hasSignal = metadata.startCommand || metadata.buildCommand || metadata.env;
    return hasSignal ? metadata : null;
  },
};
