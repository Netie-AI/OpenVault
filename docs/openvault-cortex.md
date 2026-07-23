# OpenVault ↔ Cortex integration contract

OpenVault (this repo / `openmw console`) is the **secure local API hub**.
Cortex ([Netie-AI/Cortex](https://github.com/Netie-AI/Cortex)) is the **Netie Engine**.

## Responsibilities

| Component | Owns |
|-----------|------|
| OpenVault | Encrypted key vault, continuous precheck, fallback chain, `/v1` proxy, model selection UI, SSD health |
| Cortex | Engine registry, DAG/agent runtime, T0–T3 routing, warehouse/DMS packs |

## Endpoints OpenVault calls on Cortex

| Cortex | OpenVault route |
|--------|-----------------|
| `GET /health` | `GET /api/cortex/status` |
| `GET /api/engine/*` or `/api/engine/backends` | `GET /api/cortex/engines` (graceful local fallback) |
| `GET /v1/models` or `/api/engine/models` | Merged into `GET /api/cortex/models` |

If Cortex is offline, OpenVault still serves the local OpenMW model registry and engine descriptors.

## Secure connection

- Default bind: `127.0.0.1` only
- Copy endpoint: `http://127.0.0.1:<port>/v1`
- Keys never logged; API lists return masked secrets
- Continuous precheck loop (default 60s) marks keys `ok` / `auth_fail` / `rate_limit` / `timeout` / `error`
- Fallback order: `primary → backup → cheap → free` with circuit breaker

## Local mesh (OpenIDE + Cortex)

OpenVault is the approval authority for the laptop mesh:

| Peer | Default | Announce |
|------|---------|----------|
| Cortex | `http://127.0.0.1:8000` | `POST /api/local/handshake` `peer_kind=cortex` |
| OpenIDE | `http://127.0.0.1:5100` | `POST /api/local/handshake` `peer_kind=openide` |

- Connect pack: `GET /api/local/connect-pack`
- UI: `http://127.0.0.1:5000/#mesh`
- Windows clone: `docs/local/WINDOWS_CLONE_D_DRIVE.md`
- Stub OpenIDE for bring-up: `python scripts/openide_stub.py`

## Model assignment

`PUT /api/orchestration/selection` stores:

- `primary_model`
- `fallback_models`
- `cortex_tier` (`T0`–`T3`)
- `engine_id` (`ollama` / `vllm` / `sglang` / `llama.cpp`)

This selection is what OpenVault uses to **pick and assign the best model** for assisting Cortex. Pushing keys into Cortex is always an explicit user action (not auto-exfiltrated).

## Deferred (next)

- Tool / function calling
- Agent skill scaling
- Slurm / Kubernetes multi-server orchestration
- Rust hot-path gateway inside Cortex
- Live Google OAuth token exchange (signup provider already recorded)

## Scale merge / custody

See [`OPENVAULT_SCALE_MERGE.md`](OPENVAULT_SCALE_MERGE.md) and [`openvault-account-custody.md`](openvault-account-custody.md).

Shipped in OpenVault: account custody + private relay, incident key kill/replace, OpenShip in-repo clone executor, Playwright smoke gate with artifacts.
