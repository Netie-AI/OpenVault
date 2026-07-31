# Claude vs Cursor — next split (2026-07-26)

> Gate: Claude plans/reviews; Cursor executes and pastes evidence.
> Product mindset: **users are idiots on purpose** — one button where one job exists;
> paste/upload, we detect, harvest, vault, preflight, deploy. Never invent LIVE.

---

## Priority order (highest value per effort first)

| # | Owner | Task | Why first |
|---|--------|------|-----------|
| 1 | **Cursor** | **C10** — remove `--mock-health` from both Windows launchers | Without this, every honesty fix is invisible in the stack you actually start |
| 2 | **Cursor** | **C4** — LIVE/DEMO/PLACEHOLDER badge from `profile_source` | Stops the UI from re-lying even when backend is honest |
| 3 | **Cursor** | Ship UI: call `preflight()` **before** build; show refusal in plain English | Missing wrangler/token costs 1s, not 5min |
| 4 | **Cursor** | Ship UI: auto-pick target from detect + reason chip; one Deploy button | Same one-paste pattern as vault |
| 5 | **Cursor** | Real upload bytes for library/session (endpoint that can receive a folder/zip) | Deploy cannot pretend without an artifact |
| 6 | **Cursor** | C2/C3/C1 page migration + Detection spacing | Visual vendor-parity chrome |
| 7 | **Cursor** | C7 tomli · C8 webmail · C9 portable detect tests · C6/C11/C12 | Mechanical backend hygiene |
| 8 | **Claude** | Review OmniRoute + FreeBuild feature matrix → decide what ports next | Judgement: cascade vs wrappers vs skip |
| 9 | **Claude** | ECS adapter design + bill-viz contract (real API numbers only) | Design before typing |
| 10 | **Claude** | Ad ranking policy (paid placement labelled, never fake “recommended”) | Product ethics |
| 11 | **Claude** | master.key KDF / envelope redesign | Security design, not a patch |
| 12 | **Claude** | No-admin NVMe SMART IOCTL path | Wrong offset = plausible garbage |

---

## Cursor — execute these (mechanical / known answer)

Exact files and proof steps for C1–C12 live in [`CURSOR_TASKS.md`](CURSOR_TASKS.md).
**Do C10 → C4 first**, then Ship UI items below, then the rest of C*.

### S1 — Ship: preflight in UI before build
- **Files:** `apps/web` Ship page + any ship client helper; backend already has host `preflight()`.
- **Behavior:** One Deploy path: Preflight → (only if ok) build → deploy → attach_domain.
- **Copy:** Refuse with the adapter’s reason (no token / no wrangler / empty artifact). Never green-check a simulated host.
- **Proof:** With wrangler missing, UI shows refusal *before* build; with token+wrangler, URL comes from wrangler output only.

### S2 — Ship: auto-pick target + reason
- **Files:** Ship UI + thin call to detect/stack APIs already in OpenVault.
- **Behavior:** Static/SSG → Cloudflare Pages by default; show reason (“static export, free tier, one token”). User can override; blank picker is wrong.
- **Proof:** Open a Next static fixture → target preselected with reason visible; override still works.

### S3 — Ship: upload path that can receive files
- **Files:** `OpenMW/openmw/openvault/ship/library.py` + `app.py` route; Ship UI drop zone.
- **Behavior:** Drag folder/zip → staging session → detect → Deploy. No second “confirm upload” if one drop is enough.
- **Proof:** Upload a tiny static site; `artifact_dir` non-empty; deploy path sees files.

### V1 — Vault polish only (page already rebuilt)
- Do **not** redesign Add-key / env-harvest unless Claude finds a bug in review.
- Optional Cursor: ensure Reveal always sends `X-OpenVault-Reveal: intentional` and never on mount.
- **Proof:** Network tab shows header on reveal; mount does not call `/secret`.

### Launcher / honesty
- **C10** then **C4** then **C5** (per-hop synthetic markers).

**Cursor rules:** `uv run` for Python; **npm only** for `apps/web` (D: is exFAT — no bun/pnpm). Paste evidence bundle after each task.

---

## Claude — plan / review / research (do not dump on Cursor)

### Review now (desk gate on pasted evidence)
1. Accept or reject: secret-reveal gate (loopback + intent header + audit) — update “NOT for Cursor” if accepted.
2. Accept or reject: LIVE honesty (`profile_source`) + AMD WMI / PhysicalDisk / inventory timeout=20s.
3. Review vault UI (paste/infer/precheck, env harvest banner) against vendor Key Vault UX — list gaps only.
4. Produce **FreeBuild + OmniRoute feature matrix**: each feature → Already here / Cursor port (file) / Claude design / Won’t port (why).

### Design next (Claude owns; Cursor waits for a task card)
| Topic | Claude delivers | Then Cursor |
|-------|-----------------|-------------|
| ECS host adapter | Resource graph, cost honesty, when to offer vs Pages | One module under `ship/hosts/` + tests |
| Bill visualisation | Which provider APIs, what “suggestion” may claim | UI charts + API wrappers |
| Paid target ranking | Label copy, never dress as detection | Rank field + badge in picker |
| FreeRoute / OmniRoute cascade | What subset of `combo.ts` is worth porting | Thin Python wrapper tasks |
| master.key KDF | Envelope design, migration | Implementation after sign-off |
| Middleware Gain | Kill render until A/B exists | Delete UI surface if Claude says kill |
| “OAuth at our server” | **Conflicts with no-servers model** — resolve: local OAuth device flow vs optional relay | Only after Claude picks |

### Explicitly NOT Cursor (judgement / protocol)
Keep the list in `CURSOR_TASKS.md` “NOT for Cursor”, **except**: if Claude has already landed and verified the secret-reveal gate, strike item 1 and record “accepted” in STATUS.

---

## Product copy Claude should draft (Cursor can paste into UI later)

One short Ship empty-state / success line that says the model out loud:

> Your machine builds it. Your cloud account hosts it. Your domain points at it.  
> Keys stay on this PC — OpenVault never runs your app on our servers.

---

## Shared rule

```text
Evidence beats claims. Claude reviews and plans; Cursor executes and proves.
If a Deploy/LIVE/key claim is not backed by a diff or command output, it is unverified.
```

### Message to Claude
```text
Claude: reviewer/planner only. Do not generate full patches unless asked.
Review pasted diffs/output. Update the FreeBuild+OmniRoute matrix and the ECS/bills/ad policy designs.
Tell Cursor the next single task card with exact files and proof.
```

### Message to Cursor
```text
Cursor: execute in this order — C10, C4, S1, S2, S3 — then remaining C* from CURSOR_TASKS.md.
Scoped edits only. uv run pytest / next build as proof. Paste evidence bundle. No “done” without output.
```
