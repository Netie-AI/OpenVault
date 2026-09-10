# OpenVault - Status

> Canonical "what's true now." History: [`CHANGELOG.md`](CHANGELOG.md). Deferred:
> [`PARKING_LOT.md`](PARKING_LOT.md). Map: [`docs/ACTIVE.md`](docs/ACTIVE.md).

Last reconciled: 2026-09-08. F32 shipped: #17 #16 #15 CLOSED. GUI live on
`:3010` (Electron `OpenVault` + `next start`). FreeRoute GUI posts
`/v1/chat/completions` (sealed = explicit refuse). Ship targets are in-repo
hosts only. #18 stays CLOSED. Usage $/unit NEEDS-YOU.

**UI:** `http://127.0.0.1:3010/` and `openvault app`. Webpack-dev if no
BUILD_ID. Turbopack is not used (`dev --webpack`).

## Distance

Checked: homepage, `/vault`, `/ship`, `/freeroute` (Send -> sealed refuse),
`/gate`, Electron window, `GET /api/ship/targets` (no openship_cloud /
openship.io / OPENSHIP_URL). Usage $/unit still NEEDS-YOU.

## Next

| # | Status |
|---|--------|
| Second laptop | `openvault home pack` then unpack + `openvault up` there |
| Register passkey | Vault page, unsealed: Hello / Face ID / iPhone |
| Usage $/unit | NEEDS-YOU. Display SKUs are locked (DR-0013). |

## HT gates (#18 CLOSED, boxes ticked)

HT1 `https://netie.ai/ht1-demo/`. HT2 API chat 200. HT3 passphrase-scrypt + bak
retired. HT4 Cortex healthy. HT5 inject; public `.env` 403.

## Clone-and-verify

```bash
cd OpenMW && uv run pytest tests/test_ship_cloud.py tests/test_ship_engine.py tests/test_launcher_contract.py -q
```
