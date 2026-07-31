# Cross-layer contract audit — 2026-07-25

> Evidence vs [`PRODUCT_ROLES.md`](PRODUCT_ROLES.md). App → Vault → Engine.

## Verdict

| Layer | Surface | Verdict |
|-------|---------|---------|
| App | AirGPT `:8765` + FreeIDE bridge | **PASS** — thin client; gate closed offline; FreeIDE default `:8765` |
| Vault | OpenVault `:5000` | **PASS** — keys SoT, gate, ship, FreeRoute; contract tests lock ports |
| Engine | Cortex `:8000` | **PASS w/ fixes** — uses `openvault_gate`; ping path corrected |

## AirGPT (`D:\AirGPT\FreeIDE\openvault_bridge.py`)

| Contract item | Status |
|---------------|--------|
| `/api/openvault/ping|status|mesh|connect-pack|keyvault|connect|gate` | Present (clipdrop routes) |
| FreeIDE URL default `:8765` | Pass (`openide_public_url`) |
| Gate offline → `allowed: false` | Pass |
| Keys SoT = OpenVault upsert | Pass |
| Second vault | No (env.local is cache only) |

## Cortex (`D:\Cortex\CortexOS`)

| Item | Status | Notes |
|------|--------|-------|
| `integrations/openvault_gate.py` | Pass | deny if OV down |
| Architecture `openvault_gate_required` | Pass | presets |
| Keys SoT | Pass | snapshot hydrate; no Cortex keystore |
| `workflow_openvault.ping` | **Fixed** | was `/api/openvault/ping` (AirGPT path on OV host) → now `/api/healthz` |
| FreeRoute budget fields | **Fixed on OV** | status now exports `remaining` / `remaining_tokens` aliases |
| DMS `/dms/tasks/gate/check` | N/A | warehouse task gate, not leave/deploy |

## Ports

| Port | Role |
|------|------|
| 8765 | AirGPT + FreeIDE |
| 5000 | OpenVault (+ FreeRoute `/v1`, not a separate process) |
| 8000 | Cortex |
| 5055 | Rust auth optional |
| **20128** | **External OmniRoute only** if operator runs it — OpenVault FreeRoute is `:5000/v1` |

## FreeRoute Redis+Lua

| Piece | Status |
|-------|--------|
| `vault/redis_store.py` | Done — atomic dual-bucket EVAL |
| Activate | `OPENVAULT_REDIS_URL=redis://127.0.0.1:6379/0` |
| Default | `InMemoryBucketStore` (single node) |
| Deps | `redis>=5`; tests use `fakeredis` + `lupa` |

## Remaining (honest)

1. Streaming `/v1/chat/completions` still 400.
2. Cortex should call `check_gate` on every deploy/leave path (spot-audit presets set the flag; verify all runners).
3. Enable AWS MCP (`awsiac` etc.) for Ship AWS target IaC — separate from this audit.
