# OpenVault - Status

> Canonical "what's true now." History: [`CHANGELOG.md`](CHANGELOG.md). Deferred:
> [`PARKING_LOT.md`](PARKING_LOT.md). Map: [`docs/ACTIVE.md`](docs/ACTIVE.md).

Last reconciled: 2026-09-07. GitHub OPEN = none. #12-#39 and #48 CLOSED.
#48 control plane shipped on `main` as PR #49 (`3ddcb014`). Do not rebuild.
Packs DR-0013. Desktop+Grant F20. Passkeys unseal DR-0014 / F22.
DR-0015 proposed (vault line + rust console assessed, not identity SoT).
HT1-HT5 CLEARED 2026-09-04; #18 boxes ticked 2026-09-07. Do not rebuild.

**UI:** `:3010` Compiling-proxy hang this session. Desktop: `openvault app`
or Desktop shortcut from `Install-OpenVaultDesktopShortcut.ps1`.

## Distance

Checked: homepage, `/keys`, Electron `apps/shell`, mesh handshake,
`openvault secret get`. New: WebAuthn unseal APIs + `/vault` SealBar (not
browser-clicked this session; `:3010` Compiling-proxy hang). Autofill OOS.

## Next

| # | Status |
|---|--------|
| Desktop shortcut | Run `scripts/windows/Install-OpenVaultDesktopShortcut.ps1` once |
| Register passkey | Vault page, unsealed: Windows Hello / Face ID / fingerprint, or iPhone |
| Rust console (DR-0015) | Founder: keep optional sandbox, do not move `accounts` into it |

## HT gates (#18 CLOSED, boxes ticked)

HT1 `https://netie.ai/ht1-demo/`. HT2 API chat 200. HT3 passphrase-scrypt + bak
retired. HT4 Cortex healthy. HT5 inject; public `.env` 403. Clerk: if founder
walks a later HT gate, tick GitHub in the same turn.

## Clone-and-verify

```bash
cd OpenMW && uv run pytest tests/ -q
```
