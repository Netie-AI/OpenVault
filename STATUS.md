# OpenVault - Status

> Canonical "what's true now." History: [`CHANGELOG.md`](CHANGELOG.md). Deferred:
> [`PARKING_LOT.md`](PARKING_LOT.md). Map: [`docs/ACTIVE.md`](docs/ACTIVE.md).

Last reconciled: 2026-09-10. GitHub OPEN = #61 (cites #60 Get free keys).
#12-#39, #48, #52 CLOSED (#53 squash-merged). VPC allowlist for `POST /keys/services`
(loopback + `OPENVAULT_SERVICES_ALLOW`). JWKS pin kids (#50). No public `:5000`.
Home pack: passphrase-scrypt only. Packs are DR-0016 (main already took
DR-0013 for display SKUs). Passkeys are DR-0017 (main took DR-0014 for JWKS).
DR-0015 accepted: irreversible IDs in the vault; rust console optional.
Mesh omits `#auth` when `:5055` is down. HT1-HT5 CLEARED. Do not rebuild.

**UI:** `:3010` Compiling-proxy hang. Desktop: `openvault app`. Free Keys wizard
on `/keys#free` (Groq first; GitHub Models retired; keyless parked).

## Distance

Checked: homepage, `/keys`, Electron `apps/shell`, mesh handshake,
`openvault secret get`. JWKS kids without public mint. SealBar not
browser-clicked this session (`:3010` hang). Usage $/unit still NEEDS-YOU.

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
cd OpenMW && uv run pytest tests/ -q
```
