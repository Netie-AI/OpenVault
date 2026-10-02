/**
 * IP → ISO country code, for putting a flag on each server row.
 *
 * We own the data: a GeoLite2-Country database is vendored in the repo
 * (`apps/api/assets/geoip/`) and copied into `dist/assets` at build, so
 * production reads OUR shipped copy — no third-party dependency at runtime. The
 * only network path is a last-resort download when the asset is somehow absent,
 * and it points at OUR repo (overridable via OPENSHIP_GEOIP_URL), never an
 * upstream mirror. Refreshing the vendored copy is a maintainer action
 * (`npm run update:geoip`), not something the running server does.
 *
 * Everything is best-effort: a missing DB, no network, or a private/loopback IP
 * just yields `null`, and the UI falls back to a neutral glyph.
 *
 * Modified by Netie AI, 2026: this fork does not vendor the GeoLite2-Country
 * .mmdb (apps/api/assets/geoip/ ships a README only — see NOTICE). Degradation
 * is now VISIBLE instead of silent: a one-time warning logs on first lookup
 * once the database is confirmed unavailable, and {@link countryForIp} returns
 * the sentinel string `"unknown"` (rather than `null`) for a valid IP once
 * that state is reached, so a caller can render "unknown" instead of nothing.
 */
import { open, type Reader, type CountryResponse } from "maxmind";
import { isIP } from "node:net";
import { existsSync } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const DB_FILE = "GeoLite2-Country.mmdb";

/** Fallback download source — OUR repo copy, not a third party. */
const DB_URL =
  process.env.OPENSHIP_GEOIP_URL?.trim() ||
  `https://raw.githubusercontent.com/oblien/openship/main/apps/api/assets/geoip/${DB_FILE}`;

const native = process.env.OPENSHIP_NATIVE === "true";
const CACHE_PATH = native ? null : join(homedir(), ".openship", "cache", DB_FILE);

/** On-disk locations, first existing wins: an explicit override, the vendored
 *  asset (built dist or the source tree), then a prior download cache. */
function candidatePaths(): string[] {
  const override = process.env.OPENSHIP_GEOIP_DB?.trim();
  const here = dirname(fileURLToPath(import.meta.url));
  const rel = join("assets", "geoip", DB_FILE);
  return [
    ...(override ? [override] : []),
    join(here, rel), // bundled: dist/assets/geoip
    join(here, "..", rel),
    join(here, "..", "..", rel), // source: apps/api/src/lib → apps/api/assets
    join(here, "../../../../../apps/api", rel), // relocated engine source
    join(process.cwd(), "apps", "api", rel), // run from monorepo root
    join(process.cwd(), rel), // run from apps/api
    ...(CACHE_PATH ? [CACHE_PATH] : []),
  ];
}

let readerPromise: Promise<Reader<CountryResponse> | null> | null = null;
let resolvedReader: Reader<CountryResponse> | null = null;
/** Set once we've confirmed no DB is available and won't become available. */
let dbConfirmedUnavailable = false;
let warnedMissingDb = false;

function warnMissingDbOnce(): void {
  if (warnedMissingDb) return;
  warnedMissingDb = true;
  console.warn(
    "[geo-ip] No GeoLite2-Country.mmdb found (this fork does not vendor one — " +
      "see apps/api/assets/geoip/README.md) and no fallback download succeeded. " +
      "Country lookups will report \"unknown\". Set OPENSHIP_GEOIP_DB to a local " +
      "database file, or OPENSHIP_GEOIP_URL to a mirror, to enable them.",
  );
}

/** First existing candidate, else download our copy into the cache. */
async function resolveDbPath(): Promise<string | null> {
  for (const p of candidatePaths()) {
    try {
      if (existsSync(p)) return p;
    } catch {
      /* ignore and try next */
    }
  }
  // An owned native instance uses its packaged asset. Missing optional geo data
  // must not initiate an unowned download or write into the embedding user's home.
  if (!CACHE_PATH) return null;
  try {
    const res = await fetch(DB_URL);
    if (!res.ok) return null;
    const buf = Buffer.from(await res.arrayBuffer());
    await mkdir(dirname(CACHE_PATH), { recursive: true });
    await writeFile(CACHE_PATH, buf);
    return CACHE_PATH;
  } catch {
    return null;
  }
}

function getReader(): Promise<Reader<CountryResponse> | null> {
  if (readerPromise) return readerPromise;
  readerPromise = (async () => {
    const path = await resolveDbPath();
    if (!path) {
      dbConfirmedUnavailable = true;
      warnMissingDbOnce();
      return null;
    }
    try {
      resolvedReader = await open<CountryResponse>(path);
      return resolvedReader;
    } catch {
      dbConfirmedUnavailable = true;
      warnMissingDbOnce();
      return null;
    }
  })();
  return readerPromise;
}

/**
 * Warm the reader before a batch of sync lookups. Bounded by `timeoutMs` so the
 * rare cold path (asset missing → download from our repo) never blocks the
 * request that long — flags simply appear once resolved on a later fetch.
 */
export async function primeGeo(timeoutMs = 1500): Promise<void> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    await Promise.race([
      getReader().catch(() => null),
      new Promise<void>((r) => { timer = setTimeout(r, timeoutMs); timer.unref(); }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

/** ISO-3166-1 alpha-2 (uppercase) for a literal IP, `"unknown"` once the DB is
 *  confirmed unavailable, or null. Sync + never throws; null for a bad host /
 *  private range, or while the reader is still warming (primeGeo). */
export function countryForIp(host: string | null | undefined): string | null {
  if (!host || isIP(host) === 0) return null;
  if (dbConfirmedUnavailable) return "unknown";
  if (!resolvedReader) return null;
  try {
    return resolvedReader.get(host)?.country?.iso_code ?? null;
  } catch {
    return null;
  }
}
