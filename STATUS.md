# OpenVault - Status

> Canonical "what's true now." History: [`CHANGELOG.md`](CHANGELOG.md). Deferred:
> [`PARKING_LOT.md`](PARKING_LOT.md). Map: [`docs/ACTIVE.md`](docs/ACTIVE.md).

Last reconciled: 2026-09-07. #52 `POST /keys/services`: loopback plus
`OPENVAULT_SERVICES_ALLOW` (defaults `10.128.0.3`, `34.30.222.22`). Other
custody stays loopback. No public `:5000`. #50 JWKS pin kids. #48 display
SKUs locked USD; usage $/unit still `USD NEEDS-YOU`. #13 #18 #33 CLOSED.

**UI:** `:3010` Compiling-proxy hang / `:5000` stays loopback (no public bind).
Writers: `http://35.253.229.206:8080`. JWKS:
`http://35.253.229.206:8080/.well-known/jwks.json`. Cortex prove:
`http://34.30.222.22:8010`. Demo:
`cd OpenMW && uv run --no-sync python scripts/one_seat_demo.py`

## Distance

~92%. JWKS kids without public mint. Prove VMs can register signing services
(#52) without a public `:5000`. Display SKUs locked (#48, DR-0013).
**Usage unit price is not.** FreeRoute register at `/tool/register`. Friendly
key UI at `/keys`. Service SKUs simulate-default.

## Next

| # | Status |
|---|--------|
| Usage $/unit | **NEEDS-YOU.** Not a ticket. Display SKUs are locked. DR-0009 (a) we carry provider cost |
| Deploy JWKS to prove `:8080` | Cortex bind unblocks after this ships; dms#116 is verify-only in the other repo |
| `:3010` Compiling proxy | Next hang on exFAT; HT3 used API path |

## HT gates (founder closed #18)

HT1 live `https://netie.ai/ht1-demo/`. HT2 API chat 200. HT3 passphrase-scrypt
+ bak retired + restart sealed + ship 403. HT4 Cortex status healthy. HT5
inject to `ov-env`; public `.env` 403. Homepage untouched.

## Clone-and-verify

```bash
cd OpenMW && uv run pytest tests/ -q
```
