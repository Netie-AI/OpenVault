# OpenVault — Status

> Canonical "what's true right now." History: [`CHANGELOG.md`](CHANGELOG.md). Deferred:
> [`PARKING_LOT.md`](PARKING_LOT.md). Map: [`docs/ACTIVE.md`](docs/ACTIVE.md).

Last reconciled: 2026-08-27. **HUMAN_STOP** still in force for #18 HT1-HT5.
Epic #13 F17/F18 children: #37 closed; #38 and #39 independently verified on
`origin/main` (`3030cad` / PR #43).

**UI:** `:3010` / `:5000`. Scripted demo:
`cd OpenMW && uv run --no-sync python scripts/one_seat_demo.py`

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
| Pricing / skills library | **NEEDS-YOU.** Not tickets |

## Clone-and-verify

```bash
cd OpenMW && uv run pytest tests/ -q
```
