# OpenVault - Status

> Canonical "what's true now." History: [`CHANGELOG.md`](CHANGELOG.md). Deferred:
> [`PARKING_LOT.md`](PARKING_LOT.md). Map: [`docs/ACTIVE.md`](docs/ACTIVE.md).

Last reconciled: 2026-09-05. GitHub issues #12-#39 CLOSED (no READY tickets).
Packs DR-0013. Desktop+Grant F20. Passkeys unseal DR-0014 / F22.

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
| Live Stripe pack ids | Unlock when founder creates products |

## HT gates (founder closed #18)

HT1 live `https://netie.ai/ht1-demo/`. HT2 API chat 200. HT3 passphrase-scrypt
+ bak retired. HT4 Cortex healthy. HT5 inject; public `.env` 403.

## Clone-and-verify

```bash
cd OpenMW && uv run pytest tests/ -q
```
