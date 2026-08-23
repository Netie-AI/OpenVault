---
status: accepted
date: 2026-07-24
decision-makers: Claude, founder
---

# DR-0003 - OpenVault Desktop rebuild (fork vs embed)

## Context and Problem Statement

OpenVault needed a real desktop/web UI, and there were three existing UI codebases in
play — FreeBuild's Next app (token system, UI primitives), OmniRoute's Electron shell, and
OpenVault's own minimal legacy `webui/index.html` — none of them wired exclusively to
OpenVault's FastAPI on `:5000`.

## Considered Options

- (a) Embed — wire OpenVault directly into an existing app's runtime (FreeBuild or OmniRoute)
- (b) Fork — build one new Next 16 app at `apps/web`, wearing FreeBuild's UI primitives, running none of FreeBuild's or OmniRoute's stack, wired exclusively to `:5000`
- (c) Hybrid — share significant code/runtime with FreeBuild or OmniRoute beyond just UI primitives

## Decision Outcome

Chosen option: "(b) fork, with a narrow (c) hybrid on data only" — one app at `apps/web`
wired exclusively to OpenVault's existing API, running neither FreeBuild's nor OmniRoute's
stack, with only FreeBuild's `packages/core` stack/language/workspace metadata tables
ported into Python (pure data, no runtime dependency). See DR-0005 for the honesty audit
that fed which backend routes the new UI was allowed to bind to.

## Consequences

- Good: single codebase with a single source of truth (`:5000`), no dependency on
  FreeBuild's or OmniRoute's runtime; `apps/web` is confirmed as the live app in STATUS.md.
- Bad: large one-time migration effort — the "2F files to delete outright" checklist below
  was still not 100% complete 9 days later; 4 stragglers (`apps/web/src/app/proxy` and
  `/providers` page.tsx, `apps/shell/electron/lib/resolveNodeHelper.js`,
  `apps/shell/electron/README.md`) were found and swept during the 2026-08-02 cleanup. The
  `proxy` and `providers` pages turned out to still be live in the nav (`AppBar.tsx`) and
  were kept, not removed — this document's claim that they were dead was stale.

## Confirmation

No single automated test proves the whole rebuild decision. Closest guard:
`OpenMW/tests/test_contract.py` (exists) pins the backend contract the frontend depends on.
Honest gap: `.github/workflows/ci.yml` covers `nvme_sentinel` only — there is no CI job for
`apps/web` (build or e2e).

---

## Original record (archived 2026-08-02, body preserved as-is)

# OpenVault Desktop — Rebuild Plan (single, decisive)

---

## 0. The architectural decision

**Decision: (b) fork — with a narrow (c) hybrid on *data only*.**

We build **one** Next 16 app at `D:\OpenVault\apps\web`, wearing FreeBuild's token system and UI primitives, wired **exclusively** to OpenVault's existing FastAPI on `:5000`. We run **none** of FreeBuild's stack. We run **none** of OmniRoute's stack. The hybrid part: we port FreeBuild's `packages/core` *data tables* (stacks/languages/workspaces/metadata) into Python, because those are pure data and they are the single highest-value asset in that repo.

### Why not (a) "run their stack via docker-compose and embed"

Four independent blockers, all from recon:

1. **Their build executor is proprietary and not in the repo.** `@repo/adapters` drives dockerode/ssh2/aws-sdk **plus a closed `oblien` npm package** for the SaaS build workspace. `build.service.ts` / `build-pipeline.ts` / `preflight.ts` (~190 KB) are explicitly "not liftable". Running their compose gets you a dashboard whose Deploy button cannot deploy anything we care about.
2. **Auth is mandatory and org-scoped.** `apps/dashboard/src/app/(dashboard)/layout.tsx` imports `@/lib/server/session` and **redirects to `/login`** when there is no better-auth session. Embedding it means shipping better-auth + Postgres + an org model to a single-user desktop app.
3. **The install cannot happen on this machine's repo volume.** `D:` is **exFAT with 1 MB clusters**; no hardlinks, no symlinks, no junctions (all three tested and failed). FreeBuild is a **bun workspace monorepo** (`packageManager: bun@1.3.10`, `bun.lock`, `pnpm-workspace.yaml`). Bun's default backend is hardlink; pnpm's store is hardlink. Neither can install here. OmniRoute additionally wants `better-sqlite3` native builds.
4. **Three more processes and two more databases** (Postgres, Redis, Hono API, Next dashboard, OmniRoute Next, SQLite) for a product whose pitch is "one-stop, hardcoded paths, automated for normal users."

### Why (b) works — the design system is provably clean

Recon is unambiguous: *"none of `@repo/*` are imported by any file in `src/styles/`, `src/components/ui/`, `src/components/shared/`, or `src/components/sidebar.tsx`."* The token system (`theme.css`, 479 lines, **zero imports**), `globals.css`, and 14 of the `ui/*` primitives copy with only a `cn()` repath. `@repo/ui` is dead code — **zero** dashboard files import it. So we get the entire visual system for the cost of one `postcss.config.mjs` and one import-order rule.

### What "steal OmniRoute wholesale" actually resolves to

| OmniRoute area | Verdict | Reason |
|---|---|---|
| `electron/` | **Steal wholesale (TS→JS, same language)** | `processTree.js` is copy-clean and is the single best file in the repo; `preload.js` whitelist pattern is copy-clean; `main.js` needs 6 targeted edits, all enumerated below. |
| `src/proxy.ts` + `src/server/authz/*` | **Steal wholesale into `apps/web`** | Next→Next, same framework. This is the *middleware*. Gives us loopback-only enforcement on deploy routes for free. |
| `open-sse/` LLM gateway (`combo.ts`) | **Port the algorithms, reimplement the loop — NOT wholesale** | `combo.ts` is 3629 LOC with ~45 cross-workspace imports and is flagged **"not extractable as a unit"**. OpenVault *already owns* `POST /v1/chat/completions` (app.py:1336) plus `vault/proxy.py`, `vault/fallback.py`, `vault/ratelimit.py`, `vault/redis_store.py`. Adding a second Node gateway on `:20128` means a second `node_modules` on exFAT, `better-sqlite3` native builds, 110 SQL migrations, and **a second credential store** — which directly contradicts "one-stop key vault". We port 6 leaf algorithm files into Python and reimplement the ~200-line cascade. **I am flagging this as a deliberate deviation from "wholesale."** |
| `docker/` | **Ignore** | It contains *only* the VNC+Chromium sidecar. The app image is the root `Dockerfile`/`docker-compose.yml`. Nothing there serves us. |

### Resulting process topology

```
Electron (apps/shell)  ──spawns──►  uv run openmw serve   :5000   FastAPI (the ONLY backend)
        │                └─spawns──►  next start           :3010   UI (the ONLY frontend)
        └─loads─────────────────────► http://127.0.0.1:3010
```
`:20128` (OmniRoute) and `:3001` (FreeBuild) are **removed**. No iframes anywhere.

---

## 1. Stage 0 — unblock the toolchain (BLOCKING, one agent, no parallelism)

Nothing else can start until this exits clean.

```powershell
# 1. Kill holders
Get-Process bun,node -ErrorAction SilentlyContinue | Stop-Process -Force
# 2. Nuke the broken tree (rmdir, NOT Remove-Item — exFAT)
cmd /c rmdir /s /q D:\OpenVault\apps\web\node_modules
# 3. Delete dead weight FIRST so npm has less to copy
cmd /c rmdir /s /q D:\OpenVault\apps\web\vendor-steal
Remove-Item D:\OpenVault\apps\shell\electron\package.json,`
            D:\OpenVault\apps\shell\electron\package-lock.json,`
            D:\OpenVault\apps\shell\electron\README.md -Force
# 4. Install — npm ONLY. Never bun, never pnpm.
cd D:\OpenVault\apps\web ; npm install
# 5. Verify
dir node_modules\next\package.json ; dir node_modules\.bin\next*
```

Deletions in Stage 0 (all confirmed dead by recon):
- `D:\OpenVault\apps\web\vendor-steal\` — 167 files, **zero** importers, already tsconfig-excluded.
- `D:\OpenVault\apps\shell\electron\package.json` + `package-lock.json` — OmniRoute's own `omniroute-desktop` v3.8.49 manifest declaring electron ^43 against `apps/shell/package.json`'s ^35. **This will corrupt any npm install run from `apps/shell`.**
- `D:\OpenVault\apps\shell\electron\loginManager.js`, `sqlite-inspection.js`, `types.d.ts`, `lib\resolveNodeHelper.js` — unreachable, require undeclared deps.
- `C:\Users\OoiJianHong\openvault-web\` — **diff its `src/` against `D:\OpenVault\apps\web\src\` first**, then delete. This is an active source-of-truth split.

`apps/web/package.json` is upgraded to match the vendor design system exactly:

```
next ^16.1.6, react ^19.2.4, react-dom ^19.2.4,
tailwindcss ^4.2.1, @tailwindcss/postcss ^4.2.1, postcss ^8.5.8,
clsx ^2.1.1, tailwind-merge ^3.5.0, class-variance-authority ^0.7.1,
@radix-ui/react-slot ^1.2.4, lucide-react ^0.577.0,
jose ^6, zod ^4,
@xterm/xterm ^6, @xterm/addon-fit ^0.11, @xterm/addon-web-links ^0.12,
tw-animate-css   (vendor omits it; their animate-in classes are silent no-ops)
```
Next **16** (not 15) is required so `src/proxy.ts` is the middleware entry — that is the file we are stealing from OmniRoute.

---

## 2. File-copy manifest

### 2A — FreeBuild design system → `apps/web`

| Source (absolute) | Destination (absolute) | Edits |
|---|---|---|
| `D:\OpenVault\vendor\openship\apps\dashboard\src\styles\theme.css` | `D:\OpenVault\apps\web\src\styles\theme.css` | **Copy verbatim.** Then append two new blocks: `[data-theme="glass"]` and `[data-theme="ink"]` redefining the same `--th-*` names. Do not touch existing blocks. |
| `D:\OpenVault\vendor\openship\apps\dashboard\src\app\globals.css` | `D:\OpenVault\apps\web\src\app\globals.css` | Repath `@import "../styles/theme.css"`. **Delete the `@import "../styles/fonts.css"` line entirely** (see 2A-note). **KEEP lines 18-49** (`.is-desktop` / `.app-titlebar` / `[data-app-topinset]` / `.app-sidebar-header`) — recon says delete them "unless you ship an Electron build"; we ship one, and this block is exactly the full-width draggable title bar the user asked for. Add `@import "tw-animate-css";` after `@import "tailwindcss";`. **Extend the custom variants** so skins inherit dark behaviour: `@custom-variant dark (&:where([data-theme="dark"],[data-theme="glass"],[data-theme="ink"], [data-theme="dark"] *,[data-theme="glass"] *,[data-theme="ink"] *))`. Change `--font-sans` to `ui-sans-serif, system-ui, "Segoe UI Variable Text", "Segoe UI", sans-serif`. |
| `D:\OpenVault\vendor\openship\apps\dashboard\src\styles\fonts.css` | **DO NOT COPY** | All 9 `@font-face` src urls point at `https://cdn.oblien.com/fonts/…`. Third-party CDN, unknown licence for Gellix. We ship system fonts. This is a deliberate, visible divergence from FreeBuild's typography. |
| `D:\OpenVault\vendor\openship\apps\dashboard\postcss.config.mjs` | `D:\OpenVault\apps\web\postcss.config.mjs` | Verbatim. There is no `tailwind.config.*` anywhere in their monorepo and there must not be one in ours. |
| `D:\OpenVault\vendor\openship\apps\dashboard\components.json` | `D:\OpenVault\apps\web\components.json` | Verbatim — keeps future `npx shadcn add` consistent. |
| `D:\OpenVault\vendor\openship\apps\dashboard\src\lib\utils.ts` | `D:\OpenVault\apps\web\src\lib\utils.ts` | Verbatim. |
| `…\src\components\theme-provider.tsx` | `D:\OpenVault\apps\web\src\components\theme-provider.tsx` | Widen `Theme` to `"light"\|"dim"\|"dark"\|"glass"\|"ink"\|"system"`. Replace `toggle()`'s 3-cycle with an explicit `setTheme(name)` + a `THEMES` array for the skin picker. **Keep `window.desktop.isDesktop`** (rename to `window.openvault?.isDesktop`) — we are a desktop app; desktop default = `system`, web default = `light`. **Keep `ThemeScript` verbatim in shape** — dropping it causes a light flash on every launch. |
| `…\src\components\ui\Modal.tsx` | `…\apps\web\src\components\ui\Modal.tsx` | Verbatim. This is the "Add key" modal. Requires `theme.css` present (reads `--th-card-bg-solid`). |
| `…\src\components\ui\Popover.tsx` | `…\apps\web\src\components\ui\Popover.tsx` | Verbatim. |
| `…\src\components\ui\button.tsx` | `…\apps\web\src\components\ui\button.tsx` | Verbatim. |
| `…\src\components\ui\card.tsx` | `…\apps\web\src\components\ui\card.tsx` | Verbatim. |
| `…\src\components\ui\input.tsx` | `…\apps\web\src\components\ui\input.tsx` | Verbatim. |
| `…\src\components\ui\label.tsx` | `…\apps\web\src\components\ui\label.tsx` | Verbatim. |
| `…\src\components\ui\select.tsx` | `…\apps\web\src\components\ui\select.tsx` | Verbatim. |
| `…\src\components\ui\textarea.tsx` | `…\apps\web\src\components\ui\textarea.tsx` | Verbatim. |
| `…\src\components\ui\Checkbox.tsx` | `…\apps\web\src\components\ui\Checkbox.tsx` | Verbatim. |
| `…\src\components\ui\Switch.tsx` | `…\apps\web\src\components\ui\Switch.tsx` | Verbatim (zero imports). |
| `…\src\components\ui\Tabs.tsx` | `…\apps\web\src\components\ui\Tabs.tsx` | Verbatim. `scrollbar-hide` is a no-op but harmless. |
| `…\src\components\ui\SlidingToggle.tsx` | `…\apps\web\src\components\ui\SlidingToggle.tsx` | Verbatim. Use for the Sentinel/Bottleneck segmented control. |
| `…\src\components\ui\CustomSelect.tsx` | `…\apps\web\src\components\ui\CustomSelect.tsx` | Verbatim. |
| `…\src\components\ui\DropdownMenu.tsx` | `…\apps\web\src\components\ui\DropdownMenu.tsx` | **Two required bug fixes**: replace `hsl(var(--foreground))` → `var(--foreground)` and `hsl(var(--muted))` → `var(--muted)`. This theme stores those as rgba/hex, not HSL triples; default-variant items currently render with an invalid colour. |
| `…\src\components\ui\PageContainer.tsx` | `…\apps\web\src\components\ui\PageContainer.tsx` | Change inner to `max-w-[1600px] mx-auto px-6 lg:px-8 pt-[var(--ov-topbar-h)] pb-10`. **This one edit is the fix for "Detection tab has wrong bar spacing"** — every page gets its top offset from one token instead of ad-hoc per-page padding. |
| `…\src\components\shared\WarningCallout.tsx` | `…\apps\web\src\components\ui\WarningCallout.tsx` | Verbatim (react + lucide only). Note the destination moves it under `ui/`. |
| `…\src\components\shared\OTPInput.tsx` | `…\apps\web\src\components\ui\OTPInput.tsx` | Verbatim. For mesh handshake / passkey flows. |
| `…\src\components\page-header.tsx` | `…\apps\web\src\components\ui\PageHeader.tsx` | Strip `useI18n`; props become `{title, description}`. |
| `…\src\components\toast.tsx` | `…\apps\web\src\components\ui\toast.tsx` | Strip nothing except the import path for `randomUUID`. |
| `…\src\lib\random-uuid.ts` | `…\apps\web\src\lib\random-uuid.ts` | Verbatim. |
| `…\src\components\import-project\TerminalSurface.tsx` | `…\apps\web\src\components\terminal\TerminalSurface.tsx` | Verbatim. 177 lines, guards against the known xterm zero-size crash. This is the Ship deploy log pane. |
| `…\src\lib\sseMessageProcessors.ts` | `…\apps\web\src\lib\sse\messages.ts` | Keep the `BuildMessage.type` union **verbatim** — it becomes the contract our new FastAPI SSE endpoint must emit. |
| `…\src\lib\sseClient.ts` | `…\apps\web\src\lib\sse\client.ts` | Strip `@/lib/api getApiBaseUrl` → our `OPENVAULT_API` const. |
| `…\src\utils\repoSlug.ts` | `…\apps\web\src\lib\repoSlug.ts` | Replace node `Buffer` with `btoa/atob`. |
| `…\src\components\import-project\Frameworks.tsx` | `…\apps\web\src\lib\frameworks.ts` | Strip React; keep the `frameworks` array + `stackCategories` + `getFrameworkConfig` as pure data. Icons: replace `STACK_ICONS` jsdelivr URLs with lucide letters or self-hosted SVGs (CSP in Electron will block jsdelivr). |

**Explicitly NOT copied** (recon-flagged): `sidebar.tsx` (613 lines, needs better-auth + 3 contexts + i18n — and we are building a *top-bar* layout, not a sidebar layout), `ui/FileIcon.jsx` (**broken in vendor**: `src/utils/theme.js` is a 0-byte file, throws on render), `ui/CustomCursor.tsx` (dead), `ui/IconPickerModal.tsx` (API-bound), `ui/Logo.tsx` (their brand mark), `ui/InfoBanner.jsx`, all of `shared/{PageHero,StatCard,ValueCard,FeatureCard,PlatformFeatureCard,CTASection}` (marketing, hardcoded `text-white`, off-token, use `generateIcon` → cdn.oblien.com), `packages/ui/**` (entirely — zero importers), `src/i18n/**` (we hardcode English; `interpolate()` is 5 lines, inline it if needed).

### 2B — OmniRoute Electron → `apps/shell`

| Source | Destination | Edits |
|---|---|---|
| `D:\OpenVault\vendor\OmniRoute\electron\processTree.js` | `D:\OpenVault\apps\shell\electron\processTree.js` | **Verbatim.** Windows `taskkill /PID <pid> /T /F` with array args, no shell. Without this, killing the Next child leaves grandchildren holding the exe and blocking updates. (A copy already sits in `apps/shell/electron/` — re-verify it against the vendor original, it is currently orphaned.) |
| `D:\OpenVault\vendor\OmniRoute\electron\preload.js` | `D:\OpenVault\apps\shell\electron\preload-openvault.js` | Keep the invoke/send/receive **whitelist** shape and `safeOn` returning a disposer. Delete every `login:*` channel. Channels become: `openvault:getAppInfo`, `openvault:restartServer`, `openvault:pickFolder`, `openvault:openExternal`, `onServerStatus`, `onPortChanged`, `autostart:*`. Keep the `-webkit-app-region` drag strip installer — it feeds the full-width title bar. Expose as `window.openvault`. |
| `D:\OpenVault\vendor\OmniRoute\electron\main.js` (lines: `waitForServer`, `waitForServerExit`, `createTray`, CSP block, `before-quit`, single-instance lock, autostart) | merged into `D:\OpenVault\apps\shell\electron\main-openvault.js` | **6 edits, exactly**: (1) replace `startNextServer()`'s `process.execPath + ELECTRON_RUN_AS_NODE` spawn with **two** spawns — `uv run --directory D:\OpenVault\OpenMW openmw serve --port 5000` and `npm --prefix D:\OpenVault\apps\web run start`; `resolveNodeExecutable`/`resolveServerNodePath`/the NODE_OPTIONS heap math all become dead code and are deleted. (2) port `20128` → readiness on `http://127.0.0.1:5000/api/healthz` then window at `http://127.0.0.1:3010`. (3) CSP `connect-src` → `'self' http://127.0.0.1:5000 http://127.0.0.1:3010`; **no external hosts** (this is why jsdelivr icons must be inlined). (4) secret bootstrap → write `OPENVAULT_HOME` + vault master key into `%APPDATA%\OpenVault\server.env`, and **keep the refuse-to-regenerate invariant** (see next row). (5) delete `require('./sqlite-inspection')` and `require('./loginManager')`. (6) `electron-updater` publish target → drop for pass 1 (no release channel yet). **Keep unchanged**: `waitForServer` (180 s poll), `waitForServerExit`, tray, CSP hook, single-instance lock, `before-quit` → `killProcessTree(SIGTERM)` + 5 s grace. |
| `D:\OpenVault\vendor\OmniRoute\electron\sqlite-inspection.js` | `D:\OpenVault\apps\shell\electron\vault-sealed-check.js` | Rewrite the query against OpenVault's SQLite vault table (`OpenMW\openmw\openvault\vault\store.py`, `secret_blob` column). **Keep the invariant verbatim**: if any encrypted row exists, **refuse** to auto-generate a new master key. This is the difference between "app restarts" and "every stored API key is permanently orphaned". |
| `D:\OpenVault\vendor\OmniRoute\electron\lib\resolveServerEntry.js` | `D:\OpenVault\apps\shell\electron\lib\resolveEntries.js` | Replace the two filename candidates with our uv/npm command resolution. Keep it as a pure, unit-testable function. |
| `D:\OpenVault\vendor\OmniRoute\electron\package.json` (electron-builder block only) | `D:\OpenVault\apps\shell\package.json` (merged in) | appId `com.openvault.desktop`, productName `OpenVault`, NSIS target. **Keep the two-entry `extraResources` trick** — it is what makes packaged native modules resolve outside `app.asar`. Repoint `from` at our bundle output. |
| `D:\OpenVault\vendor\OmniRoute\electron\lib\resolveNodeHelper.js` | **DO NOT COPY** | Only needed when spawning via `ELECTRON_RUN_AS_NODE`. We spawn real `uv` and `npm`. |

### 2C — OmniRoute middleware → `apps/web`

| Source | Destination | Edits |
|---|---|---|
| `D:\OpenVault\vendor\OmniRoute\src\proxy.ts` | `D:\OpenVault\apps\web\src\proxy.ts` | Swap the pipeline import. `config.matcher` → `['/', '/((?!_next/static|_next/image|favicon.ico).*)']`. |
| `D:\OpenVault\vendor\OmniRoute\src\server\authz\pipeline.ts` | `D:\OpenVault\apps\web\src\server\authz\pipeline.ts` | Strip `getCachedSettings` (db/readCache), `isDraining` (gracefulShutdown), `checkRequestIP` (open-sse ipFilter). Replace the JWT policy with a local-desktop policy: loopback-only + CSRF on mutations. Keep: route classification, CORS, body-size guard, trusted-header stripping, request-id + peer-locality stamping, OPTIONS preflight. |
| `…\src\server\authz\{classify,headers,csrf,peerStamp,types}.ts` | `D:\OpenVault\apps\web\src\server\authz\` | Copy-clean as a set (recon: "that set is self-contained"). |
| `…\src\server\authz\routeGuard.ts` | `D:\OpenVault\apps\web\src\server\authz\routeGuard.ts` | Replace OmniRoute's LOCAL_ONLY prefix list with **ours**: `/api/ship/`, `/api/deploy/`, `/api/control/action`, `/api/freeide/invoke`, `/api/engine/candidates/`. This enforces loopback **before any auth check**, so a leaked token over a tunnel still cannot trigger a deploy or a process spawn. Directly load-bearing given Ship spawns builds. |
| `…\src\shared\utils\cors.ts` + `…\authz\policies\*` | `D:\OpenVault\apps\web\src\server\authz\` | Copy the origin-allowlist helper; collapse `policies/` to one local-only policy. |

### 2D — OmniRoute routing algorithms → Python backend

These are **ports**, not copies. Destination is a new package `D:\OpenVault\OpenMW\openmw\openvault\route\`.

| Source (read as spec) | Destination | Port notes |
|---|---|---|
| `D:\OpenVault\vendor\OmniRoute\src\shared\utils\circuitBreaker.ts` | `…\openvault\route\breaker.py` | CLOSED→DEGRADED→OPEN→HALF_OPEN. Keep adaptive backoff (`resetTimeout *= 2^(cycles-3)`, cap 16×), lazy recovery (no background timer), bounded registry (500) + 5-min cold sweep. Drop the 5 `db/domainState` imports; persist via `vault/redis_store.py` if present else in-memory. Defaults: OAuth 3/60 s, API-key 5/30 s, local 2/15 s. **Only 408/500/502/503/504 trip it.** |
| `…\open-sse\services\combo\targetSorters.ts` | `…\openvault\route\sorters.py` | `select_weighted_target` (cumulative-weight roulette), `order_by_p2c` (pick 2 at random; score = successRate/100 + 1/log10(avgLatency+10), −0.25 HALF_OPEN, −inf OPEN), `sort_by_usage` (key on `execution_key` so two accounts of one model don't collapse), `sort_by_cost`. |
| `…\open-sse\services\combo\applyStrategyOrdering.ts` | `…\openvault\route\strategies.py` | Ship **8** of the 18 strategies in pass 1: `priority, weighted, fill-first, round-robin, p2c, random, least-used, cost-optimized`. Drop the 10 that need quota telemetry / manifest hints (`reset-aware, reset-window, headroom, quota-share, cache-optimized, context-optimized, context-relay, lkgp, auto, fusion/pipeline`). |
| `…\open-sse\services\combo\rrState.ts` + `…\src\shared\utils\shuffleDeck.ts` + `secureRandom.ts` | `…\openvault\route\rr_state.py` | Copy-clean. Deck-shuffle = draw without replacement so every target is used once before reshuffle. |
| `…\open-sse\services\rateLimitSemaphore.ts` | `…\openvault\route\semaphore.py` | Zero imports. Bounded FIFO queue; reject with `SEMAPHORE_QUEUE_FULL` so the cascade moves on instead of deep-queueing. |
| `…\open-sse\services\slidingWindowLimiter.ts` | `…\openvault\route\window_limiter.py` | Zero imports, 85 LOC. Merge with existing `vault/ratelimit.py`. |
| `…\open-sse\services\apiKeyRotator.ts` | `…\openvault\route\key_rotator.py` | Zero project imports. Per-connection round-robin over extra keys; mark `invalid` after 2 consecutive auth failures. Wire to `vault/store.py`. |
| `…\open-sse\services\accountFallback.ts` (signal tables + `checkFallbackError` header block only) | `…\openvault\route\fallback_signals.py` | Lift **verbatim in spirit**: `ACCOUNT_DEACTIVATED_SIGNALS`, `CREDITS_EXHAUSTED_SIGNALS`, `OAUTH_INVALID_TOKEN_SIGNALS`, `CONTEXT_OVERFLOW_PATTERNS`, `RATE_LIMIT_TEXT_PATTERNS`, and the `Retry-After` / `x-ratelimit-reset` / free-text-reset parsing ladder. Do **not** port the other ~1700 lines. |
| `…\open-sse\executors\base.ts` lines 335-424 | folded into `…\openvault\vault\proxy.py` | The credential→header contract only: `resolve_base_url`, `resolve_effective_key` (rotation), `build_headers` (`Authorization: Bearer <accessToken or key>`), and the re-resolve-on-401 behaviour at their line 1370. |
| `…\src\lib\db\encryption.ts` | **reference only** | We already have `vault/crypto.py`. **Adopt one thing**: their `credentialDecryptFailed` sentinel — when a value is still ciphertext but undecryptable (rotated key), surface it instead of coercing to an empty Bearer. And **delete the passthrough-plaintext branch equivalent** if one exists in our crypto. |
| `…\src\sse\services\auth.ts` | **reference only** | 2448 LOC of provider-specific policy. Reimplement its *contract* (`→ credentials + connection_id`, or `{all_rate_limited, retry_after}`) against our vault. Do not port. |

### 2E — FreeBuild detection data → Python

| Source | Destination | Notes |
|---|---|---|
| `D:\OpenVault\vendor\openship\packages\core\src\stacks.ts` | `D:\OpenVault\OpenMW\openmw\openvault\ship\stacks.py` | **The single highest-value file in either vendor tree.** 46 stacks × {language, category, defaultPort, install/build/start, outputDirectory, buildImage, detection{rootMarkers, deps, contentPatterns}}, 11 languages, `STACK_ROOT_MARKERS`, `OUTPUT_DIRECTORIES`, `TRANSFER_EXCLUDES`, `isUploadIgnoredPath`. Zero imports. Generate `stacks.py` with a one-shot Node script (`scripts/gen_stacks.mjs`: `import` the TS via tsx, `JSON.stringify`, emit a Python dict literal) and **commit the generated file** — do not add a build-time dependency on the vendor tree. |
| `…\packages\core\src\languages\*.ts` | `…\openvault\ship\languages.py` | Manifest→dependency parsers for js/python/go/rust/ruby/php/java/elixir/docker + `LANGUAGE_MANIFEST_FILES`. Python versions must use **real parsers** (`tomllib`, `json`, `configparser`) — not substring search. |
| `…\packages\core\src\workspaces\*.ts` | `…\openvault\ship\workspaces.py` | pnpm-workspace.yaml, package.json workspaces, Cargo, go.work, uv, rush, Maven, Gradle, `.sln`. |
| `…\packages\core\src\metadata\*.ts` | `…\openvault\ship\metadata.py` | Precedence: `openship.json` > `vercel.json` > `railway.*` > `render.yaml` (fill-only). Rename the native file to `openvault.json`. |
| `D:\OpenVault\vendor\openship\apps\api\src\lib\stack-detector.ts` | `…\openvault\ship\detect.py` (**rewrite**) | Priority-ordered rules over `stacks.py`. Add the missing `detectPackageManager()` — lockfile first (`bun.lock`→bun, `pnpm-lock.yaml`→pnpm, `package-lock.json`→npm, `yarn.lock`→yarn), then `packageManager` field, then `engines`. Python: `uv.lock`→uv sync, `poetry.lock`→poetry install, `Pipfile.lock`→pipenv, bare `requirements.txt`→`pip install -r requirements.txt`. |
| `D:\OpenVault\vendor\openship\apps\api\src\lib\project-root-detector.ts` | `…\openvault\ship\project_root.py` (**new**) | Replaces the 5-path whitelist at current `detect.py:140`. Real scan + `CANDIDATE_WEIGHTS` scoring (vercel 100 > workspace 60 > discovered 20; fullstack 30 > frontend 20 > static 10; path bonuses for `apps/web/client`, penalties for `packages/lib/shared`). **Must return `root_directory` in the response** — the current code knows the sub-path (`package.json@apps/dashboard:next`) and then throws it away. |
| `D:\OpenVault\vendor\openship\apps\api\src\modules\deployments\project-reader.ts` | `…\openvault\ship\reader.py` (**new**) | The 4-method seam: `list_directory / read_text / read_json / list_tree`. Implement `LocalReader` (fs) and `GitHubReader` (existing `ship/github_auth.py` PAT). Everything else in detection stays source-agnostic. |
| `D:\OpenVault\vendor\openship\apps\cli\src\lib\folder-deploy.ts` | reference for `…\openvault\ship\engine.py` | The complete non-git recipe. **Do not shell out to `tar`** (recon flags it broken on Windows) — use Python `tarfile`. |

### 2F — Files to delete outright

```
D:\OpenVault\apps\web\vendor-steal\                       (167 files, 0 importers)
D:\OpenVault\apps\web\src\app\proxy\page.tsx              (iframe to dead :20128)
D:\OpenVault\apps\web\src\app\providers\page.tsx          (iframe → rebuilt native)
D:\OpenVault\apps\shell\electron\main.js                  (1022 lines, unreachable)
D:\OpenVault\apps\shell\electron\preload.js               (superseded)
D:\OpenVault\apps\shell\electron\loginManager.js          (386 lines, requires paths we don't have)
D:\OpenVault\apps\shell\electron\sqlite-inspection.js     (→ rewritten as vault-sealed-check.js)
D:\OpenVault\apps\shell\electron\types.d.ts
D:\OpenVault\apps\shell\electron\lib\resolveNodeHelper.js
D:\OpenVault\apps\shell\electron\package.json             (conflicting manifest — DELETE FIRST)
D:\OpenVault\apps\shell\electron\package-lock.json
D:\OpenVault\apps\shell\electron\README.md
C:\Users\OoiJianHong\openvault-web\                       (after diffing src/)
D:\OpenVault\OpenMW\webui\index.html                      (ONLY after route-map parity is signed off — see §3)
```

---

## 3. Route / page map

`OV` = `http://127.0.0.1:5000`. Every endpoint below was verified present in `D:\OpenVault\OpenMW\openmw\openvault\app.py` at the cited line, or is marked **NEW**.

| Route | Purpose | Real endpoints (verified) | Backend gap |
|---|---|---|---|
| `/` | Overview: device health, engine status, key health, budget, mesh peers | `GET /api/healthz` (461), `/api/health/devices` (469), `/api/cortex/status` (938), `/api/keys` (666), `/api/freeroute/ratelimit` (1331), `/api/fallback/status` (933), `/api/ship/budget` (1136) | none |
| `/vault` | **One-stop key vault.** Groups by role (primary/backup/cheap/free) as collapsible bars, drag between groups, "Add key" → `ui/Modal` | `GET /api/keys` (666), `POST /api/keys` (843), `PATCH /api/keys/{id}` (859), `DELETE` (876), `POST /{id}/revoke` (883), `/rotate` (890), `GET /{id}/secret` (899), `POST /{id}/precheck` (906), `POST /api/keys/precheck-all` (911), `GET /api/keyvault/snapshot` (670), `POST /api/keyvault/upsert` (675), `POST /api/vault/seed-essentials` (1304), `GET /api/vault/env-scan` (1312), `POST /api/vault/ingest-env` (1322) | **NEW `POST /api/keys/reorder`** taking `[{id, role, priority}]`. `store.py:212-232` already supports updating `role` + `priority`; the gap is a bulk atomic endpoint so a drag-drop reorder persists in one call instead of N. Without it, drag-between-groups is optimistic-UI only. |
| `/vault/accounts` | Custody: create account, allocate relay, save-key-for-them, incident kill+replace | `GET/POST /api/accounts` (592/596), `GET /{id}` (611), `POST /{id}/relay` (620), `/{id}/keys` (627), `/{id}/incident` (642) | none |
| `/providers` | Native catalog + free-tier scan (replaces the dead iframe) | `GET /api/providers/catalog` (1256), `/free` (1266), `/coverage` (1275), `POST /{id}/downtime-check` (1280), `POST /check-all-free` (1288) | none |
| `/engine` | Models **auto-grouped by provider**, **filtered to providers whose keys pass precheck** | `GET /api/cortex/status` (938), `/engines` (948), `/models` (952), `GET/PUT /api/orchestration/selection` (956/960), `POST /api/keys/precheck-all` (911) | **NEW `GET /api/engine/catalog`** returning `{provider: {models[], key_status, last_precheck}}` server-side. Pass-1 can join `/api/cortex/models` × `/api/keys` in the client, but the join is O(providers) fetches and races the precheck loop. Server endpoint is the right answer. |
| `/engine/turbo` | **TurboQuant flow**: paste GitHub URL → clone → detect → sandbox install → benchmark vs baseline → one-click adopt | *(reuses `POST /api/detect`)* | **ALL NEW.** `POST /api/engine/candidates {git_url}` → clone+detect+isolated venv/workspace; `GET /api/engine/candidates`; `POST /api/engine/candidates/{id}/bench` → run fixed suite; `GET /api/engine/bench/baseline`; `POST /api/engine/candidates/{id}/adopt` → writes `orchestration/selection`; `POST /{id}/rollback`. See §6 for what "adopt" can and cannot mean. |
| `/sentinel` | **NVMe Sentinel — real.** Device list, SMART, temps, endurance, error log, actions | `GET /api/health/devices` (469) **← currently returns `build_demo_payload()` mock**, `GET /api/control/capabilities` (485), `POST /api/control/action` (489) | **ALL NEW, but the engine already exists in-repo** — `D:\OpenVault\nvme_sentinel\` has `inventory\discovery.list_devices()`, `telemetry\read.read_smart()` (native ioctl → DeviceIoControl → WMI fallback ladder), `commands\identify.py`, `models\smart.py` (18 typed fields incl. `percentage_used`, `available_spare`, `media_and_data_integrity_errors`, `composite_temperature_celsius`), `snapshot\collect.py`, `bench\run.py`, `stress\{fio,diskspd}.py`. Add: `GET /api/sentinel/devices`, `GET /api/sentinel/smart?device=`, `GET /api/sentinel/identify?device=`, `GET /api/sentinel/errors?device=`, `GET /api/sentinel/capabilities` (privilege/adapter probe — must report *why* it degraded), `POST /api/sentinel/snapshot`, `POST /api/sentinel/bench`. Then **repoint `/api/health/devices` off `demo_payload`.** |
| `/sentinel/bottleneck` | **Bottleneck — real.** Hop timeline SSD-Admin→driver→PCIe→CPU copy→RAM→VRAM→GPU with real durations and a real bottleneck hop | `GET /api/observe/path` (477), `GET /api/health/bottleneck` (473), `GET /api/slots` (481) | **NEW `POST /api/observe/trace`.** Today `observe/path.py:_try_live_report()` only returns live data if `OPENVAULT_HOME/last_admin_timings.json` already exists — otherwise `build_mock_path_trace_report()`. Nothing ever writes that file. The fix is small and real: `hal/base.py:52` already emits `log.info("admin_command_timing", …)` per admin command. Add an endpoint that installs a structlog capture processor, runs identify + SMART + log-page reads against the selected device, dumps the captured records to `last_admin_timings.json`, and returns `observe_path_payload(prefer_live=True)`. `Profiler\nvme_profiler\fuse.py` then fuses them into a real timeline. **The `source` field (`live`\|`mock`) must be rendered as a badge — never hide it.** |
| `/ship` | Pick repo (GitHub **or** local folder) → auto-detect → pick target → deploy | `GET /api/ship/library` (1046), `POST /api/ship/pick-folder` (1050), `/library/inspect` (1057), `/library/upload-session` (1065), `/{id}/scan` (1069), `GET /api/ship/github/status` (1073), `POST /connect` (1077), `POST/DELETE /pat` (1082/1086), `GET /repos` (1091), `/repos/{o}/{r}/branches` (1095), `POST /api/detect` (973), `GET /api/ship/targets` (1120), `POST /api/ship/blueprint` (1124), `GET/PUT /api/ship/budget` (1136/1140), `GET /api/ship/openship/status` (1155) | none for the wizard itself |
| `/ship/deploy/[id]` | Live build logs in xterm | `POST /api/ship/engine` (1099), `GET /api/ship/engine/{id}` (1113), `POST /api/deploy/one-press` (997), `POST /api/deploy/{id}/execute` (1203), `GET /api/deploy` (1191), `/api/deploy/{id}` (1184), `POST /api/deploy/{id}/playwright-smoke` (1195) | **NEW `GET /api/ship/engine/{id}/stream`** — SSE. Today it is poll-only. Must emit FreeBuild's frame contract (`type: log\|phase\|progress\|complete\|end\|error`, `data` base64, `eventId` monotonic seq assigned **before** ring-buffer trim, `step`, `stepStatus`) so the copied `TerminalSurface` + `sse/messages.ts` work unmodified. Steps: `prepare:0, clone:1, install:2, build:3, deploy:4` at progress `3,10,30,55,80` (+10 on completion). |
| `/ship/cicd` | Auto CI/CD + domain + AWS plan | `POST /api/deploy/cicd` (1180), `POST /api/deploy/domain-guide` (1170), `POST /api/ship/aws-plan` (1166), `POST /api/freebuild/plan` (1213), `GET /api/freebuild` (1225), `/{id}` (1229), `POST /{id}/execute` (1236) | none |
| `/route` | LLM proxy: strategy picker, live targets, breaker states, fallback config | `POST /v1/chat/completions` (1336), `GET/PUT /api/fallback` (916/921), `GET /api/fallback/status` (933), `GET /api/freeroute/ratelimit` (1331), `GET /api/slots` (481) | **NEW** (lands with §2D): `GET/PUT /api/route/strategy`, `GET /api/route/targets`, `GET /api/route/metrics` (per-target successRate/avgLatency/uses), `GET /api/route/breakers`, `POST /api/route/breakers/{key}/reset`. |
| `/peers` | Mesh: OpenVault ↔ Cortex ↔ FreeIDE, handshake approve, connect-pack, passkey | `GET /api/local/mesh` (500), `POST /refresh` (510), `PUT /config` (515), `POST /api/local/handshake` (529), `/{id}/decide` (539), `GET /connect-pack` (546), `GET /api/freeide/ready` (550), `POST /api/freeide/invoke` (582) | none |
| `/cloud` | Firewall rules, shares, sessions | `GET /api/cloud/rules` (738), `/devices` (742), `POST /firewall/check` (746), `GET/POST /shares` (762/766), `GET /shares/{id}` (788), `GET/POST /sessions` (795/799), `GET /{id}` (810), `POST /{id}/join` (817), `/{id}/events` (831) | none — but **deprioritise**; ship after the 5 core tabs |
| `/gate` | Policy gate check (user-triggered, **not** on mount) | `POST /api/gate/check` (697) | none |
| `/settings` | Editable paths, ports, theme/skin picker, budget, fallback, mesh config | `GET/PUT /api/ship/budget`, `GET/PUT /api/fallback`, `PUT /api/local/mesh/config` | **NEW `GET/PUT /api/settings`** for `OPENVAULT_HOME`, vendor paths, ports, `mock_health` toggle. Today the page renders hardcoded `D:\OpenVault\vendor\…` strings in markup. |

**Parity gate before deleting `OpenMW\webui\index.html`:** it has 31 endpoints wired vs ~10 today. The features that exist *only* there and must appear in the new app first: Data Flow trace, Bottleneck, Middleware Gain (`/api/fallback/status` + `/api/slots`), Account Custody, full mesh config + Approve FreeIDE / Approve Cortex / Complete sign-in / Register passkey, Cortex engine+model picker with Save, Ship GitHub flow, free-provider scanning, `seed-essentials`.

---

## 4. Layout contract (resolves the nav + skin requirements)

```
<html data-theme="light|dim|dark|glass|ink">
  <body>
    <ThemeScript/>                       ← pre-hydration, prevents flash
    <AppBar/>                            ← FULL WIDTH, fixed, h = --ov-topbar-h (56px)
    │  .app-titlebar  -webkit-app-region: drag   (from globals.css lines 18-49)
    │  [ ◯ OpenVault ] [ Overview Vault Engine Sentinel Ship Route Peers ] [ skin ▾ ] [ ─ □ ✕ ]
    <main>                               ← PageContainer supplies pt-[var(--ov-topbar-h)]
```

- **No `<aside>`.** FreeBuild's `sidebar.tsx` is not copied. The full-width bar *is* the nav *is* the Electron drag region.
- **Spacing bug fix, stated precisely:** today each page sets its own top padding while `TopNav` is `sticky; max-width:none` — Detection ends up double- or under-padded. New rule: **only `PageContainer` may set top padding**, and it reads `--ov-topbar-h`. Any page that sets its own `pt-*` is a bug.
- **Skins:** `--ov-skin` layered *after* `theme.css`. `glass.css` overrides only `--th-card-bg`, `--th-card-bd`, `--th-bg-page`, `--th-overlay`, `--th-dropdown-bg` + adds `backdrop-filter: blur(24px) saturate(180%)`. Every component keeps consuming `bg-card` / `border-border` and inherits the skin for free. Liquid glass becomes **one entry in a picker**, exactly as asked.
- **Load-bearing gotcha:** if you add a dark-based skin without adding it to the `@custom-variant dark (…)` selector in `globals.css`, every `dark:` utility in `ui/Modal.tsx` and elsewhere silently compiles to nothing. This is the #1 way to "finish" the theme work and have it be broken.

---

## 5. Stage plan — ownership is by directory, no two lanes share a file

**Rule enforced across all stages: `D:\OpenVault\OpenMW\openmw\openvault\app.py` has exactly ONE owner — the Integrator (Stage 3). Every backend lane ships an `APIRouter` in its own new file under `openvault\routers\` and touches nothing else.** The Integrator adds the `app.include_router(...)` lines in one commit.

### Stage 0 — Toolchain (SOLO, blocking, ~1–3 h)
Owner: **A0**. Owns `apps/web/{package.json,package-lock.json,tsconfig.json,next.config.mjs,postcss.config.mjs,node_modules}`, `apps/README.md`, `apps/cli/openvault_cli.py`, and all Stage-0 deletions.
Also: delete `apps/cli/openvault_cli.py` lines 25-28 (the `_HOME_WEB = Path.home()/'openvault-web'` C: escape hatch) and fix the dead `if (OMNI/'node_modules').is_dir() or True:` at line 99. Rewrite `apps/README.md` — its stated root cause (OneDrive) is **wrong**; it is exFAT.
**Exit gate:** `npm run build` succeeds in `apps/web`. Nothing else starts until this is green.

### Stage 1 — 4 parallel lanes
| Lane | Owns (exclusive) | Deliverable |
|---|---|---|
| **A — Design system** | `apps/web/src/styles/**`, `apps/web/src/app/globals.css`, `apps/web/components.json` | theme.css + glass/ink skins + globals.css with corrected `@custom-variant`. Ships a `/dev/tokens` scratch page proving all 5 themes. |
| **B — Primitives** | `apps/web/src/components/ui/**`, `apps/web/src/lib/{utils,random-uuid,repoSlug,frameworks}.ts`, `apps/web/src/components/theme-provider.tsx`, `apps/web/src/components/terminal/**`, `apps/web/src/lib/sse/**` | §2A copies with edits applied. Ships a `/dev/kitchen-sink` page. |
| **C — Detection** | `OpenMW/openmw/openvault/ship/**` (**except** `openship_client.py`), `OpenMW/openmw/openvault/routers/ship.py` (new), `OpenMW/tests/test_detect_*.py` | §2E. **Must fix all 7 recon defects**, verified by regression tests against the 16 recon cases: package-manager inference, real monorepo scan + `root_directory` in the response, Python-not-shadowed-by-Docker (current `detect.py:288` gives Node a rescue branch on bare `package.json` while Python's at `:308` only fires if Python wins the confidence race — it cannot, 0.8 vs 0.95), TOML/JSON parsing instead of substring search, the dead ternary at `:156` (`build if "build" in scripts else build`), start-command derived from real entrypoints, and `project_path=""` → 400 instead of silently resolving to the server's CWD. |
| **D — Sentinel** | `OpenMW/openmw/openvault/sentinel/**` (new), `OpenMW/openmw/openvault/observe/**`, `OpenMW/openmw/openvault/routers/sentinel.py` (new), `OpenMW/tests/test_sentinel_*.py` | Real `/api/sentinel/*` over `D:\OpenVault\nvme_sentinel`, plus `POST /api/observe/trace` writing `last_admin_timings.json`. Must surface `source: live\|mock` and a `degraded_reason` (e.g. "needs Administrator", "adapter fell back to WMI"). |

Lanes A and B touch disjoint trees. C and D touch disjoint Python packages and disjoint router files.

### Stage 2 — 3 parallel lanes
| Lane | Owns | Deliverable |
|---|---|---|
| **E — Shell + middleware** | `apps/web/src/app/layout.tsx`, `apps/web/src/components/shell/**`, `apps/web/src/proxy.ts`, `apps/web/src/server/**` | AppBar, skin picker, `PageContainer` wiring, §2C middleware. |
| **F — API client** | `apps/web/src/lib/api/**` | One typed module per backend area (`keys.ts`, `sentinel.ts`, `ship.ts`, `engine.ts`, `mesh.ts`, `route.ts`). **Every page must go through this** — today pages bypass `ovFetch` and call `fetch()` raw. |
| **G — Routing engine** | `OpenMW/openmw/openvault/route/**` (new), `OpenMW/openmw/openvault/vault/proxy.py`, `routers/route.py` (new), `OpenMW/tests/test_route_*.py` | §2D ports. |

### Stage 3 — Integrator (SOLO, ~1 h)
Owner: **A0**. Sole toucher of `app.py`: add `include_router` for ship/sentinel/route/engine/settings; add `POST /api/keys/reorder`; add `GET/PUT /api/settings`; repoint `/api/health/devices` and `/api/health/bottleneck` off `demo_payload`. **Exit gate:** every endpoint in §3 returns 200 with a real shape.

### Stage 4 — Pages, 1 route directory per agent (fully parallel, 7 lanes)
`app/page.tsx` · `app/vault/**` (+`vault/accounts`) · `app/engine/**` · `app/sentinel/**` · `app/ship/**` · `app/route/**` · `app/peers/**` + `app/gate/**` + `app/settings/**`.
No two agents share a directory. All consume Stage-1B primitives and Stage-2F clients read-only.

### Stage 5 — Desktop shell (SOLO, after Stage 4)
Owner: **A0**. Owns `apps/shell/**`. §2B. Exit gate: `npm run dist` produces an NSIS installer that launches, spawns both children, and kills the whole tree on quit.

### Stage 6 — TurboQuant harness (SOLO, last)
Owner: one agent. Owns `OpenMW/openmw/openvault/engine_candidates/**`, `routers/engine.py`, `apps/web/src/app/engine/turbo/**`.

---

## 6. What is NOT achievable in this pass — stated plainly

The user is right to be angry about being told things are done. These are the things that will **not** be done, and why.

1. **"Steal OmniRoute's proxy wholesale" — we are porting, not copying.** `combo.ts` is 3629 LOC and recon says flatly it "is not extractable as a unit — it is the single point where routing, credentials, quota, cooldowns, stickiness, metrics, webhooks and the event bus all meet." We port 6 leaf algorithm files to Python and reimplement the cascade. **8 of 18 strategies** ship; the 10 that need quota telemetry, manifest routing hints, or their `localDb` are out. The *middleware* (`src/proxy.ts` + `authz/`) **is** taken wholesale — that part is genuine.

2. **We do not get FreeBuild's deploy executor.** Their actual build machinery is `@repo/adapters` + a **proprietary `oblien` npm package** that is not in the repo. We keep our own `ship/engine.py`. What we take from them is the *detection intelligence* (46-stack table, root scoring, metadata overlay) and the *UI contract* (SSE frame shape, step/progress table). Anyone claiming we "have FreeBuild's deploys" would be lying.

3. **"Load-balanced routing" for deployed apps is not in this pass.** OmniRoute's load balancing routes *LLM requests across providers* — that we get. Load-balancing *HTTP traffic to your deployed app across replicas* requires a reverse proxy (Caddy/Traefik/nginx) with health checks and a config generator. OpenVault has no traffic manager today. `/api/ship/blueprint` and `/api/deploy/cicd` can *emit* a Caddyfile; nothing *runs* it. Separate pass.

4. **TurboQuant will not auto-implement anything.** "Paste a GitHub link and it auto-implements it into the local engine" is, in the general case, an autonomous coding task — arbitrary repo, arbitrary language, arbitrary API surface, no contract. It cannot be made deterministic. What Stage 6 actually delivers, and what you should hold me to: **clone → detect (using the new detector) → install into an isolated venv/workspace → register as a *candidate adapter* → run a fixed benchmark suite → show a side-by-side diff vs the non-turbo baseline → one-click "adopt" that writes `orchestration/selection` and is reversible by one-click rollback.** If the repo does not expose an interface our adapter shim can bind to, the UI will say so and stop. It will not silently produce a fake benchmark.

5. **Samsung-Magician parity on writes is out; reads are in.** `nvme_sentinel/telemetry/read.py` documents itself as *"Read-only: no admin commands that modify media or firmware."* Firmware update, secure erase, over-provisioning, and NVMe cache-flush admin commands are **not implemented** and are destructive-class. "Cache clean" in this pass = Windows `Optimize-Volume -ReTrim` + temp/build-cache reclamation, **dry-run first, explicit confirm, no auto-run**. Everything Sentinel *reads* (SMART, identify, error log, endurance, temps, snapshot, fio/diskspd bench) is real and already exists in-repo — that is the genuinely good news.

6. **Bottleneck will still show `mock` on machines without privileges.** The live path needs admin-command timings, which need adapter access (ioctl / DeviceIoControl), which on Windows needs elevation. Where it degrades, the UI shows the badge and the reason. We are **not** dressing mock data as live — the current `/api/health/devices` doing exactly that (`build_demo_payload`) is a large part of why the app feels fake.

7. **Gellix typography is not shipped.** Every `@font-face` in `fonts.css` points at `cdn.oblien.com`. Unknown licence, third-party host, and blocked by our own Electron CSP. The app will look close to FreeBuild but not identical.

8. **Drag-between-groups persists only after `POST /api/keys/reorder` lands** (Stage 3). Until then it is optimistic UI that resets on reload. Do not ship the Vault page claiming otherwise.

9. **Auto CI/CD does not auto-push.** `/api/deploy/cicd` can generate the workflow file; writing it into a user's GitHub repo requires the PAT with `repo` scope **and an explicit per-push confirmation**. No silent commits to user repositories.

10. **`OpenMW/webui/index.html` stays until parity.** It has 31 wired endpoints; the Next app has ~10. Deleting it before Stage 4 completes destroys the only working access to Account Custody, mesh approvals, and free-provider scanning.

11. **The `/cloud` tab is deferred** to after the five core tabs. Its endpoints are real; it is a scope call, not a capability gap.

---

## 7. Highest-risk step, and the fallback

**Highest risk: Stage 0 — installing the Next 16 / Tailwind v4 / Electron toolchain inside `D:\OpenVault\apps\web` on an exFAT volume with 1 MB clusters.**

Why this and not the theme port: everything else is downstream of it, and the failure mode is not "it looks wrong", it is "nothing builds and we regress to the `C:\Users\OoiJianHong\openvault-web` mirror" — a source-of-truth split that already exists and is one edit away from a lost-work incident. Concretely: exFAT supports **no hardlinks, no symlinks, no junctions** (all three tested and refused), which kills bun and pnpm outright; 99.7% of the 13,119 `node_modules` files are under 1 MB so each burns a full 1 MB cluster (~367 MB logical → **~12.8 GB on disk**, and `.next` pays the same tax); and npm's own rename-then-`rmdir` retire path has already failed here once, leaving the fossil `node_modules/.next-JBMzg2ew`. Adding Next 16 + Electron + xterm roughly doubles the file count.

**Fallback ladder, in order:**

1. **npm-only discipline.** Never bun, never pnpm, ever, in any lane. Always `cmd /c rmdir /s /q` (not `Remove-Item`) before reinstall. Kill every `node`/`bun` process first. Evidence this is sufficient: npm log `2026-07-25T10_48_51_387Z` shows a **real `npm install` on D: finishing `exit 0 / info ok`** against a clean tree. The failures were all against dirty trees.
2. **If npm still fails on retire/rmdir:** create a fixed **NTFS VHDX on D:** (`D:\openvault-ntfs.vhdx`, 60 GB, `Format-Volume -FileSystem NTFS`), mount it as `V:`, and **move the whole repo to `V:\OpenVault`**. This restores 4 KB clusters, hardlinks and symlinks (bun/pnpm work again, node_modules drops ~12.8 GB → ~370 MB) while the bytes physically stay on the D: spindle. Git history survives a directory move; only absolute paths in `openvault.local.json`, `apps/cli/openvault_cli.py`, and the Electron spawn commands need updating — all three are single-file edits already in scope.
3. **If VHDX is unacceptable:** move the repo to `C:` (NTFS, 291 GB free). Every symptom disappears. Recon recommends this outright. The only cost is that `D:` stops being the project drive.
4. **What we do NOT fall back to:** keeping `C:\Users\OoiJianHong\openvault-web` as a build mirror. That is the current state, it is the reason the README documents the wrong root cause, and it is a lost-work incident waiting to happen. One tree, one truth.

**Runner-up risk, with its own mitigation:** the Tailwind v4 CSS-first port. Three things silently no-op if you get them wrong — import order (`@import "tailwindcss"` → `theme.css` → `@theme inline`, and it must be `@theme inline`, not `@theme`, or vars stop re-resolving on theme change), the `@custom-variant dark/dim` binding to `[data-theme]` (not `.dark`, not `prefers-color-scheme`), and adding a new skin without extending that variant selector. **Mitigation: Lane A's exit gate is a `/dev/tokens` page that renders every `--th-*`, every `--st-*`, and every `dark:`/`dim:` utility across all five themes.** If a token resolves empty, it is visible on that page in seconds instead of two weeks later in a component nobody thought to check.