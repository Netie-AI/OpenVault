// Modified by Netie AI, 2026: the catalog is the bundled one. The upstream
// project's repository is no longer fetched at boot or every 10 minutes, and no
// remote entry overrides a bundled one. An operator can opt in to an overlay
// they control with FREEBUILD_CATALOG_URL (off by default).
import {
  APP_TEMPLATES,
  isUpstreamUrl,
  isValidAppTemplate,
  parseAppTemplate,
  templateEngineOk,
  type AppTemplate,
} from "@repo/core";
import { repos } from "@repo/db";
import { trackBackgroundWork } from "../../lib/background-work";
import { readApiVersion } from "../../lib/release-dist";

/**
 * Runtime app catalog = the BUNDLED catalog (`@repo/core` APP_TEMPLATES). By
 * default nothing else is read and no network request is made.
 *
 * Opt-in overlay: when the operator sets FREEBUILD_CATALOG_URL to a catalog they
 * control (https, or http on loopback), that file is fetched in the background
 * and its entries are merged over the bundled ones (overlay wins). The URL may
 * not point at the upstream project's repository or hosts. Stale-while-revalidate
 * and fail-safe: `getRuntimeCatalog()` is synchronous (every consumer stays sync)
 * and returns the current cache; a fetch or parse failure keeps the last-good or
 * bundled catalog, so the overlay can never break the catalog.
 *
 * Versioning (an overlay can be AHEAD of this instance): each entry declares an
 * optional `minEngine`. Resolution never silently drops a too-new app. Instead:
 *   - engine ok                         → the overlay entry (overlay wins).
 *   - too new, but bundled copy is ok    → serve the BUNDLED copy (no break),
 *                                          flagged `updateAvailable`.
 *   - too new, no runnable copy          → a guided `requiresUpdate` PLACEHOLDER
 *                                          ("Requires FreeBuild ≥ X"), not installable.
 * `schemaVersion` (an unreadable future SHAPE) is still dropped at ingest, but a
 * best-effort placeholder is surfaced when the raw entry's identity is readable.
 *
 * Trust: an overlay is whatever the operator points at. Every entry is
 * shape-validated before it can drive an install, but it is NOT signed, so only
 * point it at a file you control.
 */

export const CATALOG_URL_ENV = "FREEBUILD_CATALOG_URL";

/** The operator's overlay URL, or null (the default: bundled catalog only). */
export function catalogOverlayUrl(env: NodeJS.ProcessEnv = process.env): string | null {
  const raw = env[CATALOG_URL_ENV]?.trim();
  if (!raw) return null;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    console.warn(`[catalog] ${CATALOG_URL_ENV} is not a valid URL; serving the bundled catalog only`);
    return null;
  }
  const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  if (url.protocol !== "https:" && !(url.protocol === "http:" && loopback)) {
    console.warn(`[catalog] ${CATALOG_URL_ENV} must be https (or http on loopback); serving the bundled catalog only`);
    return null;
  }
  if (isUpstreamUrl(url.toString())) {
    console.warn(`[catalog] ${CATALOG_URL_ENV} points at the upstream project and is ignored`);
    return null;
  }
  return url.toString();
}

const TTL_MS = 600_000; // 10 minutes
/** Catalog-ENVELOPE version we know how to read (the top-level `version`). A
 *  newer envelope is logged, not fatal — entries are gated individually. */
const MAX_CATALOG_VERSION = 1;

/** A resolved catalog entry: a template, possibly a lightweight placeholder that
 *  needs a newer FreeBuild to install. */
export type ResolvedAppTemplate = AppTemplate & {
  /** Set when this id needs a newer FreeBuild than this instance, not installable. */
  requiresUpdate?: { minVersion?: string };
  /** A newer (engine-gated) version exists in the overlay; the bundled copy is served. */
  updateAvailable?: boolean;
};

/** This instance's FreeBuild version, for the `minEngine` gate.
 *
 *  Kept defensive, but note it no longer throws: `readApiVersion()` used to read
 *  package.json off disk, which threw inside the desktop's compiled binary — so
 *  this catch was silently disabling the `minEngine` gate for every desktop
 *  install. It now returns the version embedded at build time, and the gate is
 *  live on desktop too. */
function engineVersion(): string | undefined {
  try {
    return readApiVersion();
  } catch {
    return undefined;
  }
}

/** Bundled apps that pass the shape gate — the single validation path applied to
 *  the trusted set too, so a hand-edited bad bundled entry is caught at runtime. */
const bundledValid: readonly AppTemplate[] = APP_TEMPLATES.filter((app) => {
  if (isValidAppTemplate(app)) return true;
  console.warn(`[catalog] bundled app "${(app as { id?: string }).id ?? "?"}" failed shape validation — skipping`);
  return false;
});
const bundledById = new Map(bundledValid.map((b) => [b.id, b]));

/** Best-effort identity placeholder from a raw entry we couldn't fully parse
 *  (a too-new SHAPE) — the top-level identity fields are stable across schema
 *  versions, so a "requires update" card can still be shown. Null if unreadable. */
function placeholderFromRaw(raw: unknown): ResolvedAppTemplate | null {
  const r = raw as Record<string, unknown> | null;
  if (
    !r ||
    typeof r.id !== "string" ||
    typeof r.name !== "string" ||
    typeof r.description !== "string" ||
    typeof r.logo !== "string" ||
    typeof r.category !== "string"
  ) {
    return null;
  }
  return {
    id: r.id,
    name: r.name,
    description: r.description,
    kind: r.kind === "flow" ? "flow" : "template",
    logo: r.logo,
    category: r.category as AppTemplate["category"],
    available: false,
    requiresUpdate: { minVersion: typeof r.minEngine === "string" ? r.minEngine : undefined },
  };
}

/** A guided "requires update" placeholder from a validated (but too-new) template. */
function placeholder(entry: AppTemplate): ResolvedAppTemplate {
  return {
    id: entry.id,
    name: entry.name,
    description: entry.description,
    kind: entry.kind,
    logo: entry.logo,
    category: entry.category,
    available: false,
    requiresUpdate: { minVersion: entry.minEngine },
  };
}

/**
 * Resolve the effective catalog for this instance: union of bundled + overlay
 * ids (overlay wins), then the engine gate with bundled fallback / guided placeholder.
 * `tooNewRaw` are placeholders for entries whose SHAPE was too new to parse.
 */
export function resolveCatalog(
  remote: readonly AppTemplate[],
  tooNewRaw: readonly ResolvedAppTemplate[],
  engine: string | undefined = engineVersion(),
): ResolvedAppTemplate[] {
  const byId = new Map<string, AppTemplate>();
  for (const b of bundledValid) byId.set(b.id, b);
  for (const r of remote) byId.set(r.id, r); // overlay wins

  const out: ResolvedAppTemplate[] = [];
  for (const [id, entry] of byId) {
    if (templateEngineOk(entry.minEngine, engine)) {
      out.push(entry);
      continue;
    }
    // The winning entry needs a newer engine — fall back to a runnable bundled
    // copy if there is one, else surface a guided placeholder.
    const bundled = bundledById.get(id);
    if (bundled && templateEngineOk(bundled.minEngine, engine)) {
      out.push({ ...bundled, updateAvailable: true });
    } else {
      out.push(placeholder(entry));
    }
  }
  // Brand-new apps whose SHAPE was too new to parse AND aren't bundled — show a
  // "requires update" card rather than nothing.
  for (const p of tooNewRaw) {
    if (!byId.has(p.id)) out.push(p);
  }
  return out;
}

let cache: readonly ResolvedAppTemplate[] = resolveCatalog([], []);
let cachedAt = 0;
let refreshing = false;

async function fetchRemote(
  url: string,
): Promise<{ entries: AppTemplate[]; tooNew: ResolvedAppTemplate[] } | null> {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(8_000), redirect: "error" });
    if (!res.ok) return null;
    const body = (await res.json()) as { apps?: unknown; version?: unknown };
    if (!Array.isArray(body?.apps)) return null;
    if (typeof body.version === "number" && body.version > MAX_CATALOG_VERSION) {
      console.warn(`[catalog] overlay catalog version ${body.version} is newer than known (${MAX_CATALOG_VERSION}); ingesting per-entry anyway`);
    }
    // Ingest is SHAPE + schemaVersion only (no engine gate — that's the resolver's
    // job, so a too-new-engine app can fall back / guide instead of vanishing).
    // Keep the RAW object for accepted entries so forward-added fields survive.
    const entries: AppTemplate[] = [];
    const tooNew: ResolvedAppTemplate[] = [];
    for (const app of body.apps) {
      const decision = parseAppTemplate(app);
      if (decision.ok) {
        entries.push(app as AppTemplate);
      } else if (decision.reason === "schema-too-new") {
        const p = placeholderFromRaw(app);
        if (p) tooNew.push(p);
        else console.warn(`[catalog] dropping overlay app — schema too new + unreadable identity`);
      } else {
        const id = (app as { id?: string })?.id ?? "?";
        console.warn(`[catalog] dropping overlay app "${id}" — invalid shape${decision.detail ? `: ${decision.detail}` : ""}`);
      }
    }
    return entries.length > 0 || tooNew.length > 0 ? { entries, tooNew } : null;
  } catch {
    return null;
  }
}

function refresh(url: string): void {
  if (refreshing) return;
  refreshing = true;
  void trackBackgroundWork(fetchRemote(url)
    .then((remote) => {
      if (remote) cache = resolveCatalog(remote.entries, remote.tooNew);
      cachedAt = Date.now();
    })
    .catch(() => {
      /* keep last-good */
    })
    .finally(() => {
      refreshing = false;
    }));
}

/** The current app catalog (bundled, plus the operator's overlay if one is
 *  configured; engine-resolved). Sync. Makes no network request unless
 *  FREEBUILD_CATALOG_URL is set, and then refreshes in the background when the
 *  cache is older than the TTL. */
export function getRuntimeCatalog(): readonly ResolvedAppTemplate[] {
  const overlayUrl = catalogOverlayUrl();
  if (overlayUrl && Date.now() - cachedAt > TTL_MS) refresh(overlayUrl);
  return cache;
}

/** One app by id from the CURATED runtime catalog (bundled, plus the operator overlay). May be a
 *  `requiresUpdate` placeholder — callers gate install on that. Org-agnostic;
 *  use `getTemplateForOrg` when a per-org custom app should also resolve. */
export function getRuntimeTemplate(id: string | null | undefined): ResolvedAppTemplate | undefined {
  if (!id) return undefined;
  return getRuntimeCatalog().find((t) => t.id === id);
}

/** An org's custom (user-uploaded) apps as catalog entries — always unverified
 *  (provenance-based trust; the stored `verified` is ignored), and always listed:
 *  the grid is a custom app's only entry point, so an authored `unlisted` would
 *  make it unreachable. */
export async function listOrgCustomApps(organizationId: string): Promise<ResolvedAppTemplate[]> {
  const rows = await repos.customAppTemplate.listByOrg(organizationId);
  return rows.map((r) => ({ ...r.template, verified: false, unlisted: false, custom: true }));
}

/** Resolve a template by id FOR AN ORG: the curated catalog first, else the
 *  org's custom app. Curated wins (a custom app can never shadow a verified id —
 *  enforced at upload too). Custom apps are forced unverified. */
export async function getTemplateForOrg(
  organizationId: string,
  id: string | null | undefined,
): Promise<ResolvedAppTemplate | undefined> {
  if (!id) return undefined;
  const curated = getRuntimeTemplate(id);
  if (curated) return curated;
  const custom = await repos.customAppTemplate.findByAppId(organizationId, id);
  return custom ? { ...custom.template, verified: false, unlisted: false, custom: true } : undefined;
}

// Warm the operator's overlay at boot, only when one is configured. Skipped under
// vitest so importing the module never fires a real network fetch.
if (!process.env.VITEST) {
  const overlayUrl = catalogOverlayUrl();
  if (overlayUrl) refresh(overlayUrl);
}
