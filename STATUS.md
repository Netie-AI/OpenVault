# OpenVault - Status

> Canonical "what's true right now." History: [`CHANGELOG.md`](CHANGELOG.md). Deferred:
> [`PARKING_LOT.md`](PARKING_LOT.md). Map: [`docs/ACTIVE.md`](docs/ACTIVE.md).

Last reconciled: 2026-09-04. Founder closed #18 and #33 after HT1-HT5
evidence. #13 CLOSED. Pricing still NEEDS-YOU (not a ticket).

**UI:** `:3010` Compiling-proxy hang / `:5000` unsealed this session.
Scripted demo: `cd OpenMW && uv run --no-sync python scripts/one_seat_demo.py`

## Distance

~90%. Epics #13-#18 and #33 closed. Metering is in; **pricing is not**.
Friendly key UI at `/keys`. Service SKUs simulate-default.

## Next

| # | Status |
|---|--------|
| **Spaceship FTP PR** | [#47](https://github.com/Netie-AI/OpenVault/pull/47) — lint fix + merge |
| Pricing | **NEEDS-YOU.** Not a ticket. DR-0009 (a) - we carry provider cost |
| `:3010` Compiling proxy | Next hang on exFAT; HT3 used API path |

## HT gates (founder closed #18)

HT1 live `https://netie.ai/ht1-demo/`. HT2 API chat 200. HT3 passphrase-scrypt
+ bak retired + restart sealed + ship 403. HT4 Cortex status healthy. HT5
inject to `ov-env`; public `.env` 403. Homepage untouched.

## Clone-and-verify

```bash
cd OpenMW && uv run pytest tests/ -q
```
