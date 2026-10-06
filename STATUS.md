# OpenVault - Status

What is true now. History: [CHANGELOG.md](CHANGELOG.md). Deferred: [PARKING_LOT.md](PARKING_LOT.md). Map: [docs/ACTIVE.md](docs/ACTIVE.md). Rules: [AGENTS.md](AGENTS.md).

Last reconciled: 2026-10-06. Base `b4d68021`.

Building the product comes before cleanup.

## Core product

Rust console (`OpenMW/rust`, sandbox `:5055`), Electron `apps/shell`, `ship/` FreeBuild hosting, and product-role code are core. Founder ruling 2026-10-06 11:59 MYT.

[PRODUCT_ROLES.md](PRODUCT_ROLES.md) is unchanged. The 2026-10-06 audit found the cross-repo copies are not byte-identical (this file says FreeIDE; Cortex says OpenIDE).

## Now

- One vault: `E:\OpenVault\.openvault`. No second vault.
- HUMAN_STOP: do not bind `:5000` on a public or LAN interface.
- HT1-HT5 on epic 18 are cleared (founder 2026-09-04; boxes ticked 2026-09-07). The epic is closed.
- OpenMW suite is green: 1321 passed, 7 skipped, 0 collection errors.
- CI job `test-web` runs `apps/web` `npm test` on ubuntu-latest with Node 20 (`.github/workflows/ci.yml`).
- Open pull requests are #99 and #125, plus current drafts #136 and #138. All four are drafts.
- GitHub is the queue: 44 open issues at this reconcile. Landed behavior is in CHANGELOG.md.
- Usage $/unit is still NEEDS-YOU. Display SKUs stay locked (DR-0013).

## Verify

```bash
cd OpenMW && uv run pytest tests/ -q
```
