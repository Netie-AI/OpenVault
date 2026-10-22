# OpenVault - Status

What is true now. History: [CHANGELOG.md](CHANGELOG.md). Deferred: [PARKING_LOT.md](PARKING_LOT.md). Map: [docs/ACTIVE.md](docs/ACTIVE.md). Rules: [AGENTS.md](AGENTS.md).

Last reconciled: 2026-10-06. Main is `dd8f1d88`.

Building the product comes before cleanup.

## Core product

`OpenMW/rust` (Rust console), `apps/shell` (Electron), `OpenMW/openmw/openvault/ship/` (FreeBuild hosting), and product-role code are CORE. Cleanup and dead-code work never remove or edit them. Only truly dead code is removed. Building the product comes before cleanup. Founder ruling 2026-10-06 11:59 MYT.

[PRODUCT_ROLES.md](PRODUCT_ROLES.md) is unchanged. The 2026-10-06 audit found the cross-repo copies are not byte-identical (this file says FreeIDE; Cortex says OpenIDE).

## Now

- One vault: `E:\OpenVault\.openvault`. No second vault.
- HUMAN_STOP: do not bind `:5000` on a public or LAN interface.
- HT1-HT5 on epic 18 are cleared (founder 2026-09-04; boxes ticked 2026-09-07). The epic is closed.
- test-openmw 1329 passed / 7 skipped, run 37414951161 @ 19446b7b
- CI job `test-web` runs `apps/web` `npm test` on ubuntu-latest with Node 20 (`.github/workflows/ci.yml`).
- #136 merged at `2483c6c3` (apps/web source-map-js 1.2.2).
- #142 merged at `122c07e8` (unreferenced real-device smoke script deleted).
- #145 merged at `5891efc1` (offload run helpers deleted).
- #138 merged at `7ea63b0b` (intermediate revoke limited to the issuing service).
- #146 merged at `9b81b463` (access gate reads mesh peers without importing local_mesh).
- #139 merged at `dd8f1d88` (SEA-LION key import).
- #160 single-kid lease is draft PR #161. Space is the Bearer `dms:<space>`, created only by admin on loopback, lowercase. Redeem audit fails closed. Local pytest 1391 passed / 7 skipped. Not merged.
- #143 is closed unmerged, superseded by #141.
- Full tier is auth, HttpGuard, `admin_token`, keys, custody, unseal, FreeRoute routing, prove-host redeploys, and #135. The order is PR Bot CLEAR, then Security YES, then PR Bot undrafts and squashes (`--match-head-commit`), then R-0003 (Estate Verify) and Gating on the merge SHA, then a prove-host redeploy only with founder GO. A head move voids the CLEAR and Security YES.
- Open drafts from `gh pr list --state open --draft`: #99, #125, #140, #141, #144, #147, #149, #150.
- GitHub is the queue: 46 open issues at this reconcile. Landed behavior is in CHANGELOG.md.
- Usage $/unit is still NEEDS-YOU. Display SKUs stay locked (DR-0013).

## Forks (DR-0018, draft #99)

- `apps/router` ships as FreeRoute (`:3020`), from OmniRoute (MIT) @ `ae2ba35`. `apps/ship` ships as FreeBuild (`:3030`, `:3031`), from Openship (Apache-2.0) @ `aba12c8`. Both bind `127.0.0.1`. Root `THIRD_PARTY_NOTICES.md` and `apps/ship/NOTICE` carry the attribution.
- Keys: the forks read pooled keys over loopback with `X-OpenVault-Admin` and store none. Every local key write answers 501 `keys_managed_by_openvault`. Client tokens verify on `POST /api/apikeys/verify`.
- Not shipped (named 501): consumer subscription pooling, cookie/session relay, TLS or client fingerprint stealth, upstream updaters, the 9router sidecar, hosted cloud mode.
- Proven: `npm test` and `npm run test:e2e` in each fork against a real OpenVault (vault key reaches the provider; secret absent from the fork's disk). CI does not run the forks' tests yet.
- Open seams (DR-0018): router usage is not in OpenVault's ledger; the console does not pick FreeBuild or the Python `ship/` for a deploy.

## Verify

```bash
cd OpenMW && uv run pytest tests/ -q
```
