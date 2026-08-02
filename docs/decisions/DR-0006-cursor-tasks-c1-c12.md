---
status: accepted
date: 2026-07-26
decision-makers: Claude
---

# DR-0006 - Cursor work queue (C1-C12)

## Context and Problem Statement

DR-0008 set the Claude/Cursor division in principle; Cursor still needed a concrete,
unambiguous queue of mechanical, no-judgment-required tasks it could execute
independently without waiting on Claude per task.

## Considered Options

- Let Cursor pick tasks ad hoc from STATUS.md / PARKING_LOT.md
- Hand Cursor one task at a time through chat, serially
- Write an explicit C1-C12 queue up front, each item stating the exact file, change, and proof command

## Decision Outcome

Chosen option: "Explicit C1-C12 queue," under the rule "Cursor takes anything where the
answer is already known and the work is typing" — anything needing a design decision or a
"which of these three approaches" judgement stays out of the queue by construction.

## Consequences

- Good: Cursor executed independently without re-litigating design (STATUS.md later
  recorded C10 and C4 as shipped); the exFAT/npm-only rule for `D:` was captured once
  instead of re-explained per task.
- Bad: the queue is a point-in-time snapshot with ordering dependencies (C1 blocked on
  page migration) that aren't visible from STATUS.md alone once the queue itself is archived.

## Confirmation

No single test file confirms "the whole queue shipped" — it was a checklist, not a code
invariant. Honest gap: verification was per-item proof commands (`next build`,
`uv run pytest tests/ -q`) at the time, not preserved as a standing test.

---

## Original record (archived 2026-08-02, body preserved as-is)

# Cursor work queue — mechanical tasks, no research required

Split rule: **Cursor takes anything where the answer is already known and the
work is typing.** Anything needing a design decision, a protocol port, or a
"which of these three approaches" judgement stays out of this file.

Every task below states the file, the exact change, and how to prove it worked.
Do them in any order unless a task says otherwise. **Never run `bun` or `pnpm`
in this repo — `D:` is exFAT (it is a USB drive) and neither can install there.
npm only.**

Build check used throughout:
```bash
cd D:\OpenVault\apps\web && node node_modules/next/dist/bin/next build
```
Test check used throughout:
```bash
cd D:\OpenVault\OpenMW && uv run pytest tests/ -q
```

---

## C1 — Delete the legacy `.ov-*` shim once pages are migrated

**Blocked by:** the page rewrites (not yet done). Do C2 first.

`apps/web/src/styles/legacy-ov.css` is a temporary bridge. When
`grep -rn "ov-" apps/web/src/app --include=*.tsx` returns nothing, delete the
file and its `@import` in `globals.css`.

**Proof:** grep returns nothing, build exits 0.

---

## C2 — Migrate pages off `.ov-*` onto the FreeBuild primitives

23 stranded class names across 14 files. Mechanical substitution — the target
components already exist under `apps/web/src/components/ui` and
`components/shared`.

| Old class | Replace with |
|---|---|
| `ov-card` | `<Card>` from `@/components/ui/card` |
| `ov-btn` | `<Button>` from `@/components/ui/button` |
| `ov-input` | `<Input>` from `@/components/ui/input` |
| `ov-title` / `ov-page-title` | `<PageHero>` title slot |
| `ov-sub` / `ov-page-sub` | `<PageHero>` subtitle slot |
| `ov-grid` / `ov-row` / `ov-chip-row` | Tailwind `grid`/`flex` utilities |
| `ov-pill` / `ov-chip` / `ov-status-pill` | `<Badge>`-style span using `--st-*` tokens |
| `ov-main` | `<PageContainer>` |
| `ov-topnav*` / `ov-nav*` / `ov-brand` / `ov-mark` | **delete** — `AppBar` owns this now |
| `ov-detect-bar` | plain flex row; see C3 |
| `ov-iframe` | **delete the element** — iframes are out of the architecture |

**Hard rule:** no page may set its own `pt-*`. `PageContainer` is the only
thing allowed to apply top padding (it reads `var(--ov-topbar-h)`). A page with
its own top padding is the Detection-spacing bug coming back.

**Proof:** build exits 0; `grep -rn "pt-\[" apps/web/src/app --include=*.tsx`
returns nothing; every page still renders.

---

## C3 — Detection page bar spacing

`apps/web/src/app/detect/page.tsx`. Remove any local top padding and any
`sticky`/`fixed` positioning that competes with `AppBar`. Wrap the content in
`<PageContainer>` and let it supply the offset.

**Proof:** the gap between the top bar and the first row is identical on
`/detect`, `/vault` and `/ship`. Compare screenshots at the same zoom.

---

## C4 — Surface the LIVE / DEMO badge honestly

The backend now returns two new fields on `/api/health/devices`:
`profile_source` (`live` | `fallback_flag` | `fallback_substituted`) and
`profile_degraded_reason` (string or null).

Render a badge driven by `profile_source`:
- `live` → green "LIVE"
- `fallback_flag` → grey "DEMO (--mock-health)"
- `fallback_substituted` → amber "PLACEHOLDER HARDWARE" + show
  `profile_degraded_reason` as tooltip or caption

**Do not** render "LIVE" unless `profile_source === "live"`. This is the exact
bug that displayed an RTX 4050 on a machine with an AMD iGPU.

**Proof:** with `--mock-health` the badge says DEMO; without it, LIVE.

---

## C5 — Per-hop mock labelling on the Bottleneck view

`path_trace.hop_timeline` hops are still synthetic on the GPU side even when
SSD-side timings are real. Add a per-hop `is_synthetic` indicator in the UI
wherever the backend marks it. A single response-level badge is not enough
once half the hops are real — that is how a half-real view reads as fully real.

**Proof:** GPU hops visibly marked while SSD hops are not.

---

## C6 — Remove the duplicate fallback-status endpoint from the client

`GET /api/fallback/status` is byte-identical to `GET /api/fallback`
(see `docs/BACKEND_HONESTY_AUDIT.md` §4). Pick one in
`apps/web/src/lib/api/route.ts`, delete the other wrapper, keep a comment
saying they were duplicates.

**Proof:** build exits 0.

---

## C7 — Add `tomli` fallback for Python 3.10

`OpenMW/pyproject.toml` declares `requires-python ">=3.10,<3.13"`, but
`openmw/openvault/ship/languages.py` imports `tomllib`, which is stdlib only
from 3.11. On 3.10 the import fails, the module sets it to `None`, and **all
TOML detection silently returns empty** — `pyproject.toml`, `Cargo.toml`,
`Pipfile` all stop being detected with no error.

Add `tomli>=2.0; python_version < "3.11"` to the dependencies and import it as
the fallback. Then make the failure loud rather than silent-empty.

**Proof:** `uv run pytest tests/ -q` passes; TOML detection tests still green.

---

## C8 — Add the missing `webmail` stack

`openmw/openvault/ship/stacks.py` has 45 entries; the vendor table at
`vendor/openship/packages/core/src/stacks.ts` has 46. The missing key is
`webmail`. Port that one entry faithfully, then add
`assert len(STACKS) == 46` to the test so the count cannot drift from the claim.

**Proof:** `uv run pytest tests/ -q` passes.

---

## C9 — Make detection regression tests machine-portable

`OpenMW/tests/test_detect_stacks.py` hardcodes `D:\OpenVault\vendor\openship`
and `D:\Cortex` behind a `pytest.skip` guard. On any other machine those skip
silently, so CI goes green having tested nothing.

Rebuild them as `tmp_path` fixtures reproducing the same structure:
- a bun monorepo (`bun.lock`, `packageManager: bun@…`, workspaces) with a Next
  app at `apps/dashboard`
- a Python project with `pyproject.toml` + `uv.lock` + `docker-compose.yml`

Keep the real-tree tests as an **additional** opt-in check, not the only one.

**Proof:** `uv run pytest tests/ -q -rs` shows zero skips for these cases.

---

## C10 — Windows launchers stop forcing demo mode

`scripts/windows/Start-LocalMesh.ps1:50` and
`scripts/windows/Start-NetieStack.ps1:101` both pass `--mock-health`, so the
shipped stack runs in demo mode by default. Remove the flag from both; keep it
available as an opt-in switch for screenshots.

**Proof:** launch via either script, hit `/api/health/devices`, confirm
`profile_source: "live"`.

---

## C11 — Delete the stale C: mirror

`C:\Users\OoiJianHong\openvault-web` is a duplicate of `apps/web/src`
(verified byte-identical when checked). It is a source-of-truth split waiting
to cause lost work. The in-repo app now builds, so delete the directory.

**Proof:** `apps/web` still builds; nothing references the old path
(`grep -rn "openvault-web" D:\OpenVault --include=*.py --include=*.ts
--include=*.tsx --include=*.md`).

---

## C12 — `openvault doctor` in CI-friendly form

`apps/cli/openvault_cli.py` has a `doctor` command. Add `--json` so it can be
machine-read, and make the exit code non-zero when a required check fails.

**Proof:** `python apps/cli/openvault_cli.py doctor --json` emits valid JSON.

---

# NOT for Cursor — these stay with the research track

Listed so they do not get picked up by mistake. Each needs a judgement call or
a protocol port, and getting one subtly wrong is worse than not doing it.

1. **~~Unauthenticated secret-reveal~~** — **landed pending Claude accept:** loopback +
   `X-OpenVault-Reveal: intentional` + audit. **master-key KDF/envelope** still Claude-only.
2. **The no-admin NVMe SMART path** — `IOCTL_STORAGE_QUERY_PROPERTY` with
   `StorageDeviceProtocolSpecificProperty` / `NVMeDataTypeLogPage`. Raw
   `DeviceIoControl` structs; a wrong offset yields plausible garbage.
3. **Making Bottleneck GPU hops live** — SSD path can be honest; GPU-side hops and
   “Middleware Gain” need an A/B harness before they may render as live.
4. **Porting OmniRoute's routing cascade** (`combo.ts`, 3629 LOC) to Python —
   Claude picks subset first ([`AGENT_SPLIT.md`](AGENT_SPLIT.md)).
5. **Repointing `/api/health/devices` off `build_demo_payload`** onto the real
   sentinel engine — touches `app.py`, which has a single owner (Claude gates).
6. **TurboQuant candidate/benchmark/adopt harness.**
7. **Deciding what "Middleware Gain" should show.** It cannot come from a
   sensor; it needs an A/B harness. Until then it should not render at all.
8. **ECS adapter + bill viz + paid ad ranking** — design in Claude, then Cursor
   gets a one-module task card (see [`SHIPPING_MODEL.md`](SHIPPING_MODEL.md)).

**Ship/Vault next for Cursor (after C10/C4):** **S1–S3 / V1** in
[`AGENT_SPLIT.md`](AGENT_SPLIT.md) — preflight-before-build, auto-pick target,
real upload bytes. One Deploy button; no simulated host success.
