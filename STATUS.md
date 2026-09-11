# OpenVault - Status

> Canonical "what's true now." History: [`CHANGELOG.md`](CHANGELOG.md). Deferred:
> [`PARKING_LOT.md`](PARKING_LOT.md). Map: [`docs/ACTIVE.md`](docs/ACTIVE.md).

Last reconciled: 2026-09-11. GitHub OPEN = #60 (Get free keys). #12-#39, #48,
#52, #61, #62 CLOSED. No public `:5000`. VPC allowlist for
`POST /keys/services` (loopback + `OPENVAULT_SERVICES_ALLOW`). JWKS pin kids
(#50). Home pack: passphrase-scrypt only. Packs are DR-0016, passkeys DR-0017.
DR-0015 accepted: irreversible IDs in the vault; rust console optional.
Mesh omits `#auth` when `:5055` is down. HT1-HT5 CLEARED. Do not rebuild.

**UI:** `http://127.0.0.1:3010/` and `openvault app`. The Compiling-proxy hang
is fixed: Turbopack is not used (`dev --webpack`), prod uses `next start` when
`.next/BUILD_ID` exists, and readiness waits for real HTML instead of a bound
port. Free Keys wizard on `/keys#free` (Groq first; GitHub Models retired).
Ship targets are in-repo hosts only (DR-0003).

## Branch estate (2026-09-11 merge wave)

17 branches were unmerged against `origin/main`. Measured, not assumed: 14 are
squash-merge ghosts - their pull requests landed, so their commits never became
ancestors of main, and git reports them "ahead" while carrying no content main
lacks. Two more (PRs #5, #7) were closed unmerged and hold only retired
scaffolding. Ghost deletion is NOT independently verified - do not delete on
this note alone.

## Known-red - do not report this suite as green

`OpenMW` has 5 collection errors, so those files contribute zero coverage
(R-0002): `test_key_channel_quant`, `test_kv_quant`, `test_prefetch_flash`,
`test_prefetch_sparsity`, `test_run`. They are all quant / prefetch / run
files, so one shared root cause at one binding point is likely (R-0004).

## Distance

Checked: `waitForServer` 9/9, including a new gate proving a build-in-progress
shell at HTTP 200 is refused and named "compiling", not "unreachable".
`nvme_sentinel` 96 passed, 6 skipped (needs real NVMe). NOT checked: a full
`OpenMW` suite here, the `apps/web` build, the 14 ghosts. Usage $/unit NEEDS-YOU.

## Next

| # | Status |
|---|--------|
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
