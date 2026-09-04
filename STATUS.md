# OpenVault - Status

> Canonical "what's true right now." History: [`CHANGELOG.md`](CHANGELOG.md). Deferred:
> [`PARKING_LOT.md`](PARKING_LOT.md). Map: [`docs/ACTIVE.md`](docs/ACTIVE.md).

Last reconciled: 2026-09-04. #18 stays OPEN until human passphrase (HT3) +
restart-sealed check. HT1 live URL is up. #33 stays OPEN. #13 CLOSED.

**UI:** `:3010` / `:5000`. Scripted demo:
`cd OpenMW && uv run --no-sync python scripts/one_seat_demo.py`

## Distance

~85%. Epics #14-#17 and #13 closed. Demo #18 open (HT3 human). Metering is in;
**pricing is not**. Friendly key UI at `/keys`. Service SKUs simulate-default.

## Next

| # | Status |
|---|--------|
| #18 DEMO epic | HT1+HT5 live evidence; HT3 needs human passphrase then Lock/Unseal/Retire bak |
| #33 EPIC host+meter | Open - children closed; HT1 URL exists, epic stays open per founder |
| **Spaceship FTP PR** | Branch open — adapter + recommend/preflight/engine; await merge (not on origin/main yet) |
| Pricing | **NEEDS-YOU.** Not a ticket. DR-0009 (a) - we carry provider cost |

## HT1 / HT5

Live `https://netie.ai/ht1-demo/` shows `OpenVault HT1 sample`. Homepage
`https://netie.ai/` untouched. HT5: Groq inject wrote 1 vault env name to
`ov-env`; public `.env` refused. Live overwrite still needs
`OPENVAULT_SPACESHIP_ALLOW_PUBLISH=1` (human).

## Friendly key UI (PR #42)

Subscribe / Bring your key / Free keys / Operator at `/keys`.

## Clone-and-verify

```bash
cd OpenMW && uv run pytest tests/ -q
```
