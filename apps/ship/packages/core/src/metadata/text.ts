/**
 * Small string helpers shared across the metadata parsers, kept in one place so
 * the vercel/render/railway parsers don't each carry their own copy.
 */

/** Strip a UTF-8 BOM if present (Windows-saved manifests). */
export function stripBom(content: string): string {
  return content.charCodeAt(0) === 0xfeff ? content.slice(1) : content;
}

/** A trimmed, non-empty string, or undefined. */
export function trimmed(value: unknown): string | undefined {
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : undefined;
}

/**
 * Split file content into lines, tolerant of a leading BOM and CRLF endings.
 * Splitting on `\r?\n` (not `\n`) matters for the hand-rolled toml/yaml readers:
 * a trailing `\r` would break a `key = value` / `key: value` match — `.` never
 * matches `\r`, and `$` won't anchor before it — silently dropping the line.
 */
export function splitLines(raw: string): string[] {
  return stripBom(raw).split(/\r?\n/);
}
