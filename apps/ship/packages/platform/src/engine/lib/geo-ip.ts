/**
 * IP → ISO country code, for putting a flag on each server row.
 *
 * Modified by Netie AI, 2026: FreeBuild ships no GeoLite2-Country database and
 * downloads none from the upstream project. Country lookups read a local file
 * (OPENSHIP_GEOIP_DB) or a database the operator supplied at a mirror they run
 * (OPENSHIP_GEOIP_URL, cached under ~/.openship/cache). With neither set, no
 * network request is made and lookups report "unknown". A URL that points at the
 * upstream project is refused.
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
import { isUpstreamUrl } from "@repo/core";

const DB_FILE = "GeoLite2-Country.mmdb";

/**
 * The operator's own mirror of the database, or null. There is no default: the
 * upstream project's repository is never a source, and a configured URL that
 * points at it is ignored.
 */
export function geoipDownloadUrl(): string | null {
  const raw = process.env.OPENSHIP_GEOIP_URL?.trim();
  if (!raw) return null;
  if (isUpstreamUrl(raw)) {
    console.warn("[geo-ip] OPENSHIP_GEOIP_URL points at the upstream project and is ignored.");
    return null;
  }
  return raw;
}

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
      "see apps/api/assets/geoip/README.md) and no operator mirror is configured. " +
      "Country lookups will report \"unknown\". Set OPENSHIP_GEOIP_DB to a local " +
      "database file, or OPENSHIP_GEOIP_URL to a mirror you operate, to enable them.",
  );
}

/** First existing candidate, else the operator's mirror (if any) into the cache. */
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
  const url = geoipDownloadUrl();
  if (!CACHE_PATH || !url) return null;
  try {
    const res = await fetch(url);
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
 * rare cold path (asset missing → operator mirror download) never blocks the
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
