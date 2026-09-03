# OpenVault — Status

> Canonical "what's true right now." History: [`CHANGELOG.md`](CHANGELOG.md). Deferred:
> [`PARKING_LOT.md`](PARKING_LOT.md). Map: [`docs/ACTIVE.md`](docs/ACTIVE.md).

Last reconciled: 2026-08-27. **HUMAN_STOP** still in force for #18 HT1-HT5.
Epic #13 F17/F18 children: #37 closed; #38 and #39 independently verified on
`origin/main` (`3030cad` / PR #43). Skills SoT is Netie-KB `:8030`
([`DR-0012`](docs/decisions/DR-0012-skills-kb-crew-wiring.md)); OpenVault
`POST /api/crew/gate` shipped. Cortex-crew merge blocked (repo 404).

**UI:** `:3010` / `:5000`. Scripted demo: `cd OpenMW && uv run --no-sync python scripts/one_seat_demo.py`

## Distance

~78%. Epics #14-#17 closed. #13 custody reopen: bak retire + PM CSV + agent
retrieve verified on main; epic stays open. Demo #18 open until HT1-HT5.
FreeBuild has four hosts; no live box has run it (HT1). Metering is in;
**pricing is not**.

## HUMAN_STOP

Auto-orch loop **stopped** on https://github.com/Netie-AI/OpenVault/issues/18 HT1-HT5.
Founder authorized #13 under HUMAN_STOP (2026-08-22 yes-now). Do not claim HT1-HT5 done.

## Next

| # | Status |
|---|--------|
| #18 DEMO epic | Awaits your HT1-HT5 |
| #13 EPIC custody | F17/F18 children verified; epic stays open (do not close) |
| #33 EPIC host+meter | Open — children closed; blocked on #18 HT1 |
| #38 PM CSV ingest | **CLOSED.** Independent verify 8/8 on origin/main |
| #39 agent retrieve | **CLOSED.** Independent verify 3/3; hard-denies PAN |
| **Skills / KB / crew** | Keys=OpenVault. Skills=Netie-KB `:8030`. Cortex stirs. `POST /api/crew/gate` shipped. Cortex-crew merge blocked (404) |
| Pricing | **NEEDS-YOU.** Not a ticket. DR-0009 (a) — we carry provider cost |

## Friendly key UI (PR #42, brought up to main 2026-09-03)

Subscribe / Bring your key / Free keys / Operator live in the Next console at `/keys`
(`apps/web/src/app/keys/page.tsx`; the retired `OpenMW/webui` Keys tab was ported there).
Subscribe shows a Cortex API key only (`ov_` framed as Cortex, `provider=cortex`). BYOK shows
the provider name the user pasted. Free is Register then Install. Operator hop status stays
hop-honest (R-0011) on `/vault`. Account-issued Cortex keys are `custody=tenant` (DR-0009).

**Proof:** `cd apps/web && npm test` (copy / render / BYOK locks) and
`cd OpenMW && uv run pytest tests/test_key_ui.py -q`. Loopback proof server:
`npm run serve` binds `127.0.0.1:3010` (set `KEY_UI_PORT` to run beside `next dev`).
HT2-HT5 stay HUMAN_STOP. Do not mint Cortex#42.

## Clone-and-verify

```bash
cd OpenMW && uv run pytest tests/ -q
```
