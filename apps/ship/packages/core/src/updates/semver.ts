/**
 * Minimal semver utilities — patch-level compare + range matching, no deps.
 * Prerelease/build suffixes are ignored (releases are stable X.Y.Z). This is
 * the single source of truth; the desktop updater used to duplicate it.
 */

/** Parse "v1.2.3-rc.1" → [1, 2, 3] (leading v + prerelease/build stripped). */
export function parseSemver(v: string): [number, number, number] {
  const core = (v ?? "").trim().replace(/^v/i, "").split(/[-+]/)[0] ?? "";
  const parts = core.split(".").map((n) => parseInt(n, 10) || 0);
  return [parts[0] ?? 0, parts[1] ?? 0, parts[2] ?? 0];
}

/** -1 if a<b, 0 if equal, 1 if a>b. */
export function compareSemver(a: string, b: string): number {
  const pa = parseSemver(a);
  const pb = parseSemver(b);
  for (let i = 0; i < 3; i++) {
    if (pa[i]! > pb[i]!) return 1;
    if (pa[i]! < pb[i]!) return -1;
  }
  return 0;
}

/**
 * Does `version` satisfy `range`? `range` is a space-separated list of
 * comparators, ALL of which must hold (AND): `<=`, `>=`, `<`, `>`, `=`, or a
 * bare version (exact). Examples: "<=0.1.8", ">=0.1.0 <0.1.9", "0.1.8".
 * Returns false for an empty or unparseable range.
 */
export function satisfiesRange(version: string, range: string): boolean {
  const comparators = (range ?? "").trim().split(/\s+/).filter(Boolean);
  if (comparators.length === 0) return false;
  return comparators.every((comp) => {
    const m = comp.match(/^(<=|>=|<|>|=)?\s*(.+)$/);
    if (!m) return false;
    const op = m[1] ?? "=";
    const cmp = compareSemver(version, m[2]!);
    switch (op) {
      case "<": return cmp < 0;
      case "<=": return cmp <= 0;
      case ">": return cmp > 0;
      case ">=": return cmp >= 0;
      case "=": return cmp === 0;
      default: return false;
    }
  });
}
