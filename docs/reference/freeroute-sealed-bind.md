# FreeRoute sealed bind (dms#116 verify remount)

Cite only: [dms#116](https://github.com/Netie-AI/dms/issues/116). This file
does **not** implement DMS. After OpenVault publishes JWKS kids, a *different*
verify agent remounts the live SQL/MySQL run.

## What Cortex prove needs

Cortex `POST /v1/contract/jwks/refresh` is a cold-path JWKS fetch after DMS
mints an OpenVault intermediate. It cannot bind if JWKS has no kids, and it
must not mint -- public `POST /keys/services` is loopback-only.

| Probe (2026-09-07, before this change) | Result |
|---|---|
| `GET http://35.253.229.206:8080/.well-known/jwks.json` | 404 |
| `GET http://35.253.229.206:8080/keys/jwks` | `{"keys":[]}` |
| `GET http://35.253.229.206:8080/keys/root` | 200, `kid=root-...` (public pin) |
| `GET http://35.253.229.206:8080/api/keys` | 200, `{"keys":[]}` -- vault list, **not JWKS** |
| `POST http://35.253.229.206:8080/keys/services` | 403 loopback-only (keep) |
| `GET http://34.30.222.22:8010/health` | 200 `{"status":"ok","pack":"dms"}` |

## Env keys (Platform bind)

Do not put `ov_` tokens in git, tickets, logs, or chat.

```text
OPENVAULT_PROVE_BASE=http://35.253.229.206:8080
OPENVAULT_JWKS_URI=http://35.253.229.206:8080/.well-known/jwks.json
OPENVAULT_JWKS_ALT=http://35.253.229.206:8080/keys/jwks
OPENVAULT_ROOT_URI=http://35.253.229.206:8080/keys/root
CORTEX_PROVE_URL=http://34.30.222.22:8010
LIVE_KEY_ID=119691f2c637
LIVE_KEY_SECRET_MANAGER=openvault-dms-writer-token
```

`LIVE_KEY_ID` is the issued FreeRoute caller id (12 hex). The token is already
sealed in Secret Manager. Optional: set the same id on the prove VM as
`LIVE_KEY_ID` or `OPENVAULT_LIVE_KEY_ID` so `/api/system/bind` can echo the id
(it refuses values that start with `ov_`).

Public `:5000` bind stays off. Writers use the prove host above, not `0.0.0.0`.

## Bind sequence (OpenVault side)

1. Serve `GET /.well-known/jwks.json` with at least the trust-root kid
   (`netie_verify_only: true`). No mint required.
2. Keep `POST /keys/services` and `POST /keys/intermediate` loopback/VPC.
3. When DMS later mints on the VM, `int-*` kids appear next to the root pin.
4. Cortex refreshes JWKS and verifies chain against the pinned root.

## dms#116 (verify-only, other repo)

After the JWKS kids exist, the dms#116 agent remounts the live SQL Server and
MySQL verify run. It must:

- Fetch JWKS from `OPENVAULT_JWKS_URI` (kids present).
- Confirm public mint is still 403.
- Use Secret Manager for the writer token; never paste `ov_`.
- Write no OpenVault or DMS product code in that verify run.

Usage `$/unit` stays NEEDS-YOU. DR-0013 display SKUs are unrelated to this pin.
