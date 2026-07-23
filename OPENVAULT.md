# OpenVault — organised map

Product surface lives beside the nvme-sentinel measurement core.

## Layout

| Path | What |
|------|------|
| `OpenMW/openmw/openvault/` | Python OpenVault: vault, custody, deploy, OmniRoute, OpenShip, **local mesh** |
| `OpenMW/rust/openvault-console/` | Rust passkey auth + vault + OmniRoute/OpenShip test UI (`:5055`) |
| `OpenMW/webui/` | Liquid-glass console UI (`openmw console` → `:5000`) |
| `docs/local/` | Local mesh + Windows `D:\OpenVault` clone guide |
| `docs/openvault-*.md` | Cortex, custody, providers, Rust console contracts |
| `scripts/windows/` | `Clone-OpenVault.ps1`, `Start-LocalMesh.ps1` |
| `openvault.local.example.json` | Copy to `openvault.local.json` on the laptop |

## Local perfect connection

```
OpenIDE :5100  ──handshake/invoke──►  OpenVault :5000  ◄──deploy/engines──  Cortex :8000
                                         │
                                         └── passkeys/UI ──► Rust :5055
```

1. Clone to **`D:\OpenVault`** via `scripts/windows/Clone-OpenVault.ps1`
2. Start Cortex + OpenIDE + `Start-LocalMesh.ps1`
3. Open **http://127.0.0.1:5000/#mesh** → Approve Cortex + OpenIDE
4. Use connect pack at `D:\OpenVault\.openvault\connect_pack.json`

## Commands

```bash
# Python console + mesh
cd OpenMW && uv run openmw console --cortex-url http://127.0.0.1:8000 --openide-url http://127.0.0.1:5100

# Rust auth UI
cd OpenMW/rust/openvault-console && cargo run --release
```
