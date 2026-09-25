# OpenVault - Status

> Canonical "what's true now." History: [`CHANGELOG.md`](CHANGELOG.md). Deferred:
> [`PARKING_LOT.md`](PARKING_LOT.md). Map: [`docs/ACTIVE.md`](docs/ACTIVE.md).

Last reconciled: 2026-09-25. GitHub OPEN includes #70 (LOCAL-1). #12-#39, #48,
#52, #61, #62 CLOSED. No public `:5000`. HT1-HT5 CLEARED. Do not rebuild.
JWKS pin kids (#50). Packs DR-0016, passkeys DR-0017. DR-0015 accepted.

**UI:** `http://127.0.0.1:3010/` and `openvault app`. Free Keys wizard on
`/keys#free`. Ship targets in-repo only (DR-0003).

LOCAL-1 (#70): `local_qwen` is a FreeRoute hop with no cloud key. Contract:
`served_provider` / `served_model` / `served_local` on chat JSON; request
`local_only`; 503 `openvault_local_only_unavailable`. Not local-proven until a
served Cortex run shows `served_local=true` with zero outside-model calls.

## Known-red - do not report this suite as green

`OpenMW` has 5 collection errors, so those files contribute zero coverage
(R-0002): `test_key_channel_quant`, `test_kv_quant`, `test_prefetch_flash`,
`test_prefetch_sparsity`, `test_run`. Quant / prefetch / run; one shared root
cause at one binding point is likely (R-0004).

## Distance

Checked: `waitForServer` 9/9. `nvme_sentinel` 96 passed, 6 skipped (needs NVMe).
Usage $/unit NEEDS-YOU.

## Next

| # | Status |
|---|--------|
| #70 LOCAL-1 | OpenVault hop + served_* + fail-closed local_only. Ceiling: merged, local not proven. |
| OpenMW collection errors | 5 files, one root-cause class. Blocks a green suite. |
| Verify ghosts | R-0003: a different run must confirm before any deletion. |
| apps/web build | No CI job exists for it; `npm run build` unverified. |
| Usage $/unit | NEEDS-YOU. Display SKUs are locked (DR-0013). |

## HT gates (#18 CLOSED, boxes ticked)

HT1 `https://netie.ai/ht1-demo/`. HT2 API chat 200. HT3 passphrase-scrypt + bak
retired. HT4 Cortex healthy. HT5 inject; public `.env` 403.

## Clone-and-verify

```bash
cd OpenMW && uv run pytest tests/ -q
```
