---
status: accepted
date: 2026-07-25
decision-makers: Claude
---

# DR-0004 - Cross-layer contract audit

## Context and Problem Statement

AirGPT, OpenVault, and Cortex were developed in parallel against PRODUCT_ROLES.md's
contract without a step that actually verified the three layers still agreed with each
other and with the contract.

## Considered Options

- Trust the docs as written, audit only when something breaks in the field
- Write a full automated contract test suite from scratch before shipping more features
- Manual evidence-based audit now, route by route, promoting the durable checks into an automated test

## Decision Outcome

Chosen option: "Manual evidence-based audit now," because it was cheap (one pass), found
two real bugs immediately (Cortex's `workflow_openvault.ping` hit the wrong path; FreeRoute
budget status was missing `remaining`/`remaining_tokens` aliases Cortex expected), and
seeded `tests/test_contract.py` as the durable, ongoing guard instead of a one-time report.

## Consequences

- Good: two real cross-layer bugs fixed same-day; the audit's checks now run continuously
  in `tests/test_contract.py` rather than needing to be re-run by hand.
- Bad: a point-in-time snapshot — the "Remaining (honest)" section already listed 3 open
  items (streaming `/v1`, per-runner gate-check spot audit, AWS MCP) at time of writing that
  needed separate follow-up.

## Confirmation

`OpenMW/tests/test_contract.py` (exists).

---

## Original record (archived 2026-08-02, body preserved as-is)

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
