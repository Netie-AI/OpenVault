# Claude execution asks — queues, RAG honesty, AirGPT async UI

> Generated 2026-07-27 after Cursor shipped OpenVault **Slice 1** (attempt
> classifier + park). Evidence: `uv run pytest tests/test_attempt_policy.py
> tests/test_openvault.py -q` → **15 passed**; `mypy --strict` clean on
> `route/attempt.py`.
>
> Design authority: [`DESIGN_TIERED_QUEUE_LB.md`](DESIGN_TIERED_QUEUE_LB.md).
> ClipDrop authority: [`CLIPDROP_CONTRACT.md`](CLIPDROP_CONTRACT.md).
> AirGPT lane: `D:\AirGPT\docs\who-does-what.md`.

Claude: act as reviewer/planner only unless a decision requires a short
spec patch. Do **not** rewrite Cursor's Slice 1 unless evidence shows a bug.
Paste decisions into `docs/CLAUDE_DECISIONS.md` and reply with a Cursor card
per open item.

---

## Ask A — Tiered queue product gates (blocks Slice 2+)

Read `docs/DESIGN_TIERED_QUEUE_LB.md` §10. Decide, with one paragraph each and
a single recommended default:

### A1. Detached jobs vs proxy-only
Does `X-OpenVault-Queue: async` exist? If **no**, Q2 collapses into a longer
Q1 and cold persistence is cut. If **yes**, state who owns the process when
the window closes (Electron tray? Task Scheduler?).

### A2. Cold-tier spend ethics
May Q2 retry a **paid** key minutes after the user left? Options: free/cheap
only · spend cap · explicit opt-in header. Honesty posture of this repo
suggests no silent paid spend — confirm the rule.

### A3. Killed attempts vs key health
Recommendation in design: count attempt-timeouts against the **job**, never
the key (local Ollama can exceed 60s honestly). Sign off or override.

### A4. Duplicate delivery
When a cancelled attempt may already have been billed upstream, what does
the UI say, and do we allow replay from Q3?

### Deliverable
A short § in `CLAUDE_DECISIONS.md` titled **Tiered queue gates** with A1–A4
answers. Then a Cursor card for **Slice 2 only** (in-memory Q0+Q1,
kill-and-send at 3 hard fails, injected clock) — or "defer Slice 2 until …"
if A1 kills cold queue and you want balancer first.

---

## Ask B — Review Slice 1 evidence (desk review)

Review these claims against the pasted design + Cursor's files (do not
re-run unless inconsistent):

1. Three 429s → hop `closed`, `failures==0`, `park_until` set (regression for
   the live bug in §1.1).
2. `Retry-After: 30` → park ≈ 30s via `parse_upstream_retry_hint_ms`.
3. Generic 401 → `quarantine_key` + `job=continue_chain` (two-axis mapping).
4. `non_retryable` → `job=dead`, no health mutation.
5. Proxy: 429 on primary then 200 on backup succeeds; primary parked not opened.

**Files:** `route/attempt.py`, `vault/fallback.py` (`record_park`),
`vault/proxy.py`, `tests/test_attempt_policy.py`.

Flag: any case where `check_fallback_error` and `classify_attempt` disagree
dangerously; any place proxy still blanket-`record_failure`s.

---

## Ask C — AirGPT Space 5 / RAG honesty (your lane)

From the handoff (already partially shipped by Cursor on AirGPT settings):

1. **Live-verify Space 5 (Good Good)** after purge / `chats_as_evidence: false`.
   Re-ask Netie / JEPA. Paste: question, top cites, whether any cite is a
   `chat_*.md` (must be zero).
2. **Fabrication scrub on chat-ingest** — plan the check that refuses or
   strips assistant-invented citations before freeze. File paths in
   `D:\AirGPT\rag\ingest.py` (or adjacent). Do **not** touch create-toast /
   Docs canvas (already done).
3. **Ingest-time authority column** — schema + write path; how retrieve
   weights it vs goal. Spec only until Cursor is free of OV queue work.
4. **File-scoped retrieve** — when user @-mentions a file, ranking must not
   leak other spaces' chat residue.

Stay in AirGPT. **You own `index.html` this session** if UI is needed for
verify; Cursor stays out of it.

---

## Ask D — AirGPT async create (Cursor will implement backend; you specify UI)

Cursor will (next AirGPT session, **not** this OpenVault session unless you
move roots) make `POST .../sources` return `{ job_id }` immediately
(`clipdrop.py` + `rag/ingest.py`), mirroring build-stream threading.

**You specify:**
1. Poll contract for `index.html` (`pollRagIngestJob`): interval, timeout,
   stage labels, failure copy (one sentence cause + next move — same rule as
   ClipDrop contract §4).
2. Whether create-toast "View" opens Sources with the live job, or a global
   job drawer.
3. Media Phase 2/3 acceptance: captions vs OCR — which ships first, what
   "not enough data" looks like, and the refuse rule when vision/OCR deps
   are missing (honest skip, not fake text).

Do not invent a second job store. `rag/store.py` `ingest_jobs` already exists.

---

## Ask E — Load balancer default (after A1)

Design §6 recommends **least-used within role band** before falling through
roles. Confirm or pick `p2c` / weighted. State whether vault
`ordered_candidates` is replaced or wrapped. One Cursor card for Slice 3
only after Slice 2 ships (or after A1 collapses tiers).

---

## Sequencing (so nobody rebuilds)

| Order | Owner | Work |
|------:|-------|------|
| 0 | Cursor | **Done** — Slice 1 attempt/park |
| 1 | Claude | Asks A + B (gates + Slice 1 review) |
| 2 | Claude | Ask C Space 5 live verify |
| 3 | Cursor | AirGPT async `/sources` backend (no `index.html`) |
| 4 | Claude | Ask D UI poll spec → Cursor wires `index.html` when you release the lane |
| 5 | Cursor | Slice 2 Q0/Q1 after Ask A |
| 6 | Cursor | Slice 3 balancer after Ask E |

**Already done — do not rebuild:** create toast + View, prior-RAG import,
injection refuse, Docs MD/canvas, authority/goal modules, evidence policy
toggle + purge chat sources, ClipDrop A1–A4 + Settings clipboard gate,
DPAPI master.key (412 passed).

---

## Message back to Cursor

After A/B: paste decisions + "Slice 2 card" or "defer".
After C: paste Space 5 evidence (cites).
After D: paste poll UI contract; release `index.html` lane when ready.
