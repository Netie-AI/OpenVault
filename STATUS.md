# OpenVault - Status

> Canonical "what's true now." History: [`CHANGELOG.md`](CHANGELOG.md). Deferred:
> [`PARKING_LOT.md`](PARKING_LOT.md). Map: [`docs/ACTIVE.md`](docs/ACTIVE.md).

Last reconciled: 2026-09-07. #48 SYSTEM control plane: display SKUs locked
and labeled USD (`USD10` / `$10`); usage $/unit still `USD NEEDS-YOU`.
#13 #18 #33 CLOSED. Spaceship FTP on main via
[#47](https://github.com/Netie-AI/OpenVault/pull/47) `2feaf1eb`.

**UI:** `:3010` Compiling-proxy hang / `:5000` stays loopback (no public bind).
Writers: `http://35.253.229.206:8080`. Demo:
`cd OpenMW && uv run --no-sync python scripts/one_seat_demo.py`

## Distance

~90%. Epics #13-#18 and #33 closed. Metering is in. Display SKUs are locked
(#48, DR-0013). **Usage unit price is not.** Friendly key UI at `/keys`.
Service SKUs simulate-default.

## Next

| # | Status |
|---|--------|
| Usage $/unit | **NEEDS-YOU.** Not a ticket. Display SKUs are locked. DR-0009 (a) we carry provider cost |
| `:3010` Compiling proxy | Next hang on exFAT; HT3 used API path |

## HT gates (founder closed #18)

HT1 live `https://netie.ai/ht1-demo/`. HT2 API chat 200. HT3 passphrase-scrypt
+ bak retired + restart sealed + ship 403. HT4 Cortex status healthy. HT5
inject to `ov-env`; public `.env` 403. Homepage untouched.

## Clone-and-verify

```bash
cd OpenMW && uv run pytest tests/ -q
```
