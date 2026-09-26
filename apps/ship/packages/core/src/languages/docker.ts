import type { LanguageDetector, PortDetectionContext } from "./types";

/**
 * Docker - the manifest is the Dockerfile itself. We don't extract deps from
 * it (those live inside the image layers, opaque from the text). What we DO
 * recover is the listening port from an `EXPOSE` directive, used as the
 * default port when no explicit setting is provided.
 *
 * If you need the structured workspace plan (parsed RUN/COPY/ENV instructions),
 * use the dockerfile compiler in `@repo/adapters` - that's a separate concern.
 */
function parseDockerfilePort(content?: string): number | null {
  if (!content) return null;
  const m = content.match(/^EXPOSE\s+(\d{2,5})/m);
  if (!m) return null;
  const port = parseInt(m[1], 10);
  return port > 0 && port <= 65535 ? port : null;
}

function detectPortFromDockerfile(context: PortDetectionContext): number | null {
  return parseDockerfilePort(context.fileContents?.["dockerfile"]);
}

export const dockerLanguageDetector: LanguageDetector = {
  id: "docker",
  label: "Docker",
  manifestFiles: ["dockerfile"],
  // Dockerfiles don't surface a dep map - opaque to text-only inspection.
  parseManifest: () => ({}),
  detectPort: detectPortFromDockerfile,
};
