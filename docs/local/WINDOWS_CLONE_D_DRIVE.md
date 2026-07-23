# Clone OpenVault to `D:\OpenVault` (Windows)

This machine (cloud agent) cannot write to your `D:` drive. Run these on the Windows host.

## 1) Clone + organise

In PowerShell:

```powershell
irm https://raw.githubusercontent.com/Netie-AI/OpenVault/cursor/openvault-local-mesh-aa83/scripts/windows/Clone-OpenVault.ps1 -OutFile $env:TEMP\Clone-OpenVault.ps1
# or from an existing checkout:
powershell -ExecutionPolicy Bypass -File scripts\windows\Clone-OpenVault.ps1
```

Default target: **`D:\OpenVault`**.

Creates:

```
D:\OpenVault\                 # git checkout
D:\OpenVault\.openvault\      # keys, mesh, connect_pack
D:\OpenVault\.data\cortex\
D:\OpenVault\.data\openide\
D:\OpenVault\openvault.local.json
```

## 2) Start local mesh

Start Cortex on `:8000` and OpenIDE bridge on `:5100` the usual way, then:

```powershell
cd D:\OpenVault
powershell -ExecutionPolicy Bypass -File scripts\windows\Start-LocalMesh.ps1 -WithRustAuth
```

Opens OpenVault at **http://127.0.0.1:5000/#mesh**, approves Cortex + OpenIDE, writes `D:\OpenVault\.openvault\connect_pack.json`.

## 3) Perfect connection checklist

| Peer | URL | Approve |
|------|-----|---------|
| OpenVault | http://127.0.0.1:5000 | self |
| Cortex | http://127.0.0.1:8000 | Local Mesh → Approve Cortex |
| OpenIDE | http://127.0.0.1:5100 | Local Mesh → Approve OpenIDE |
| Rust auth | http://127.0.0.1:5055 | optional passkey UI |

OpenIDE should:

1. `GET http://127.0.0.1:5000/api/local/connect-pack`
2. `POST http://127.0.0.1:5000/api/local/handshake` with `peer_kind=openide`
3. Use `POST /api/openide/invoke` for `complete_signin` / `register_passkey`

Cortex should:

1. Read connect pack `openvault.v1` / `deploy_from_cortex`
2. Announce with `peer_kind=cortex`
3. Call `POST /api/deploy/from-cortex` for deploy-to-web
