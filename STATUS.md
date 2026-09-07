# OpenVault - Status

> Canonical "what's true now." History: [`CHANGELOG.md`](CHANGELOG.md). Deferred:
> [`PARKING_LOT.md`](PARKING_LOT.md). Map: [`docs/ACTIVE.md`](docs/ACTIVE.md).

Last reconciled: 2026-09-07. GitHub OPEN = none. #12-#39, #48, #52 CLOSED
(#53 squash-merged, VPC allowlist for POST /keys/services).
Owned-laptop replica: `openvault home pack` / `unpack` (passphrase-scrypt only).
#48 control plane shipped on `main` as PR #49 (`3ddcb014`). Do not rebuild.
Packs DR-0013. Desktop+Grant F20. Passkeys unseal DR-0014 / F22.
DR-0015 accepted: irreversible IDs in the vault; rust console stays optional.
Mesh omits `#auth` when `:5055` is down. Pointer phone (DR-0004) stays blocked.
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
| Second laptop | `openvault home pack` then unpack + `openvault up` there |
| Register passkey | Vault page, unsealed: Windows Hello / Face ID / fingerprint, or iPhone |
| Rust console | Optional sandbox. Python `accounts` stay put. Do not add phone-verify. |

## HT gates (#18 CLOSED, boxes ticked)

HT1 `https://netie.ai/ht1-demo/`. HT2 API chat 200. HT3 passphrase-scrypt + bak
retired. HT4 Cortex healthy. HT5 inject; public `.env` 403. Clerk: if founder
walks a later HT gate, tick GitHub in the same turn.

## Clone-and-verify

```bash
cd OpenMW && uv run pytest tests/ -q
```
