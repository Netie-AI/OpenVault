# OpenVault - Status

> Canonical "what's true right now." History: [`CHANGELOG.md`](CHANGELOG.md). Deferred:
> [`PARKING_LOT.md`](PARKING_LOT.md). Map: [`docs/ACTIVE.md`](docs/ACTIVE.md).

Last reconciled: 2026-09-04. **HUMAN_STOP** still on #18 HT1-HT5 for live
homepage / paid chat / live publish. Local `:3010`/`:5000`/`:8010` are up.
#13 CLOSED. Skills SoT is Netie-KB `:8030`
([`DR-0012`](docs/decisions/DR-0012-skills-kb-crew-wiring.md)).

**UI:** `:3010` / `:5000`. Scripted demo:
`cd OpenMW && uv run --no-sync python scripts/one_seat_demo.py`

## Distance

~80%. Epics #14-#17 and #13 closed. Demo #18 open until HT1-HT5. Metering is in;
**pricing is not**. Friendly key UI at `/keys`. Service SKUs simulate-default.

## Next

| # | Status |
|---|--------|
| #18 DEMO epic | Awaits HT1-HT5 (local UI evidence in progress; live Spaceship still gated) |
| #33 EPIC host+meter | Open - children closed; blocked on #18 HT1 |
| **Skills / KB / crew** | Keys=OpenVault. Skills=Netie-KB `:8030`. `POST /api/crew/gate` shipped |
| Pricing | **NEEDS-YOU.** Not a ticket. DR-0009 (a) - we carry provider cost |

## Friendly key UI (PR #42)

Subscribe / Bring your key / Free keys / Operator at `/keys`. Subscribe shows a
Cortex `ov_` key only. BYOK labels the pasted provider. Free is Register then
Install. Account-issued Cortex keys are `custody=tenant` (DR-0009).

## Service SKUs (PR #40)

`STRIPE_MODE=simulate` default. SKUs `ov_hosted` $24 / `ov_fast` $79 / `byo_*` $9.
Not verified: live card charge, netie.ai DNS, `OPENVAULT_SHIP_MODE=live`.

## Clone-and-verify

```bash
cd OpenMW && uv run pytest tests/ -q
```
