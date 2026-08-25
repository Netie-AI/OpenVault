# OpenVault — Status

> Canonical "what's true right now." History: [`CHANGELOG.md`](CHANGELOG.md). Deferred:
> [`PARKING_LOT.md`](PARKING_LOT.md). Map: [`docs/ACTIVE.md`](docs/ACTIVE.md).

Last reconciled: 2026-08-25. **HUMAN_STOP** still in force for #18 HT1-HT5. Epic #13 was
reopened 2026-08-22 (F17 bak + F18 CSV/retrieve). #37/#38/#39 re-landed on `main`. Skills
SoT is Netie-KB `:8030` ([`DR-0012`](docs/decisions/DR-0012-skills-kb-crew-wiring.md));
OpenVault `POST /api/crew/gate` shipped. Cortex-crew merge blocked (repo 404).

**UI:** `:3010` / `:5000`. Scripted demo: `cd OpenMW && uv run --no-sync python scripts/one_seat_demo.py`

## Distance

~75%. Epics #14-#17 closed. #13 custody reopen in flight (bak retire + PM CSV + agent
retrieve). Demo #18 open until HT1-HT5. FreeBuild has four hosts; no live box has run it
(HT1). Metering is in; **pricing is not**.

## HUMAN_STOP

Auto-orch loop **stopped** on https://github.com/Netie-AI/OpenVault/issues/18 HT1-HT5.
Founder authorized #13 under HUMAN_STOP (2026-08-22 yes-now). Do not claim HT1-HT5 done.

## Next

| # | Status |
|---|--------|
| #18 DEMO epic | Awaits your HT1-HT5 |
| #13 EPIC custody | Reopened F17/F18. #37 re-land + #38 + #39 |
| #33 EPIC host+meter | Open — children closed; blocked on #18 HT1 |
| #38 PM CSV ingest | Implemented; independent verify still open |
| #39 agent retrieve | Implemented; hard-denies PAN |
| **Skills / KB / crew** | Keys=OpenVault. Skills=Netie-KB `:8030`. Cortex stirs. `POST /api/crew/gate` shipped. Cortex-crew merge blocked (404) |
| Pricing | **NEEDS-YOU.** DR-0009 (a) — we carry provider cost |

## Clone-and-verify

```bash
cd OpenMW && uv run pytest tests/ -q
```
