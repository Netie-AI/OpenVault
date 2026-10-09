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
- #160 single-kid lease is draft PR #161. Owner Space and tenant are bound on assign. Redeem writes lease_redeem without the ref or the secret. Local pytest 1386 passed / 7 skipped. Not merged.
- #143 is closed unmerged, superseded by #141.
- Full tier is auth, HttpGuard, `admin_token`, keys, custody, unseal, FreeRoute routing, prove-host redeploys, and #135. The order is PR Bot CLEAR, then Security YES, then PR Bot undrafts and squashes (`--match-head-commit`), then R-0003 (Estate Verify) and Gating on the merge SHA, then a prove-host redeploy only with founder GO. A head move voids the CLEAR and Security YES.
- Open drafts from `gh pr list --state open --draft`: #99, #125, #140, #141, #144, #147, #149, #150.
- GitHub is the queue: 46 open issues at this reconcile. Landed behavior is in CHANGELOG.md.
- Usage $/unit is still NEEDS-YOU. Display SKUs stay locked (DR-0013).

## Verify

```bash
cd OpenMW && uv run pytest tests/ -q
```
