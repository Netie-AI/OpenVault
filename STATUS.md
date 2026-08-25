# OpenVault — Status

> Canonical “what’s done / what’s next.” Deferred ideas: [`PARKINGLOT.md`](PARKINGLOT.md).
> Protocol/architecture: [`implementation_plan.md`](implementation_plan.md).

Last reconciled: 2026-08-22.

---

## One app

OpenVault (`:5000`) is the local control plane:

**see red hotspot → acknowledge model slots → hold keys → ship → mesh into Cortex → gated fix**

| Tier | Package | Role |
|------|---------|------|
| Health | `openmw.openvault.health` | Laptop device inventory |
| Observe | `openmw.openvault.observe` | PathTrace hops + severity (`hot` = red) |
| Vault | `openmw.openvault.vault` | Encrypted keys, accounts, proxy |
| Ship | `openmw.openvault.ship` | Deploy / OpenShip / email gates |
| Mesh | `openmw.openvault.mesh` | Local mesh + Cortex client + `/api/slots` |
| Control | `openmw.openvault.control` | GPU/CPU/fan remediation (dry_run default) |

Libraries (unchanged at repo root): `nvme_sentinel/`, `Profiler/`. Peer engine: Cortex at `http://127.0.0.1:8000` (run from `D:\Cortex`).

---

## APIs (new)

| Route | Purpose |
|-------|---------|
| `GET /api/observe/path` | Hop timeline + `severity` / `reach` for UI hotspots |
| `GET /api/slots` | All local + Cortex model slots acknowledged |
| `GET /api/control/capabilities` | Honest capability probe |
| `POST /api/control/action` | `dry_run` default; writes need `confirm: true` |
| `GET /api/openfree/ratelimit` | OpenFree token-budget snapshot (tier, remaining, reset) |
| `GET /api/vault/env-scan` | Which env vars can be auto-vaulted (masked values only) |
| `POST /api/vault/ingest-env` | Auto-import env secrets into the vault (`dry_run` default) |

Legacy: `GET /api/health/bottleneck` still works (aliases observe).

---

## Local mesh runbook

Ports: OpenVault `5000` · Rust auth `5055` · Cortex `8000` · OpenIDE `8765` (AirGPT).

1. Start Cortex yourself (e.g. from `D:\Cortex`) on `:8000`.
2. `powershell -ExecutionPolicy Bypass -File scripts\windows\Start-LocalMesh.ps1` (optionally `-WithRustAuth`).
3. Open http://127.0.0.1:5000/#mesh → approve peers if needed.
4. Connect pack: `.openvault/connect_pack.json`.

`Start-LocalMesh` does **not** start Cortex/OpenIDE. Sync: `cd OpenMW && uv sync` (separate from root sentinel / Profiler syncs).

---

## nvme-sentinel — done (v0.1.0)

HAL/adapters/CLI/bench/CI green. Interview gate P1–P6 complete.

---

## Small Software LAN cloud (2026-07-23)

Pivot toward “Cloud for Small Software” + multiplayer agents — **LAN-first**, not AWS/Azure.

| Piece | Status |
|-------|--------|
| `/api/cloud/devices` | Done — ARP + local iface discovery |
| `/api/cloud/firewall/check` | Done — bypass/force → WARN + **deny** |
| `/api/cloud/shares` | Done — publish/list share codes (no secrets) |
| `/api/cloud/sessions` | Done — multiplayer agent session stub |
| Gate bypass hardening | Done — `bypass`/`force`/`skip_rules` never honored |
| AirGPT OpenIDE Share LAN | Done — `openideShareLanApp()` |
| Stress | `python scripts/stress_four_mesh_playwright.py` (API + tabs) |
| Rust `:5055` | Skipped when `cargo` missing |

OpenIDE URL in mesh: **`http://127.0.0.1:8765`** (AirGPT), not stub `:5100`.

---

## OpenFree gateway + auto-vault (2026-07-24)

OpenVault owns free-gateway routing for **OpenFree** (PRODUCT_ROLES). The gateway
now budgets cost, not just requests.

| Piece | Status |
|-------|--------|
| Dual-bucket limiter (`vault/ratelimit.py`) | Done — request bucket (QPS) + token bucket (`prompt + max_tokens`) |
| Smooth refill | Done — continuous refill, no fixed-window boundary burst |
| Reserve → refund | Done — reserves `max_tokens` up front, refunds the unused remainder; failed upstream refunds in full |
| Tiers | Done — `local` (unmetered loopback) / `free` / `pro`, via `X-OpenFree-Tier` |
| `429` + `Retry-After` | Done on `POST /v1/chat/completions` |
| `X-RateLimit-Tier/Limit/Remaining/Reset` | Done on every gateway response |
| Auto-vault from env (`vault/env_ingest.py`) | Done — scans credential-shaped env vars, skips placeholders, stores encrypted; secrets never echoed |
| Redis + Lua bucket store | **Not done** — `BucketStore` protocol is the seam; `InMemoryBucketStore` is single-node only |
| Streaming (`stream: true`) | **Not done** — still `400`; reserve/refund logic is already stream-shaped |

Identity comes from `X-OpenFree-Identity` (falls back to client host); budgets are
per identity per tier.

---

## Layer contract conformance (2026-07-24)

App layer (AirGPT `:8765` / OpenIDE) → custody layer (OpenVault `:5000`) →
engine layer (Cortex `:8000`). Locked by `tests/test_contract.py`.

**Fixed — contract drift:** the mesh defaulted OpenIDE to `:5100`, the legacy
standalone stub, so the connect pack (the shared wiring doc every peer reads)
published a dead URL. AirGPT serves OpenIDE on **`:8765`**. `DEFAULT_PORTS` /
`OPENIDE_DEFAULT_URL` in `mesh/local_mesh.py` are now the single source of
truth; `app.py`, `cli.py`, `Start-LocalMesh.ps1`, the example config, the webui
and the docs all derive from it. `scripts/openide_stub.py` stays on `:5100` —
it *is* the stub, and is now opt-in rather than the default.

**Added — missing contract endpoint:** `GET /api/openide/ready` preflights
OpenIDE Run (keys + mesh approval + gate) and states `keys_source_of_truth:
openvault`, per the keys lock in PRODUCT_ROLES.

`tests/test_contract.py` asserts: default ports match the cheat-sheet, every
bridge/gate route is served, the connect pack pins `:8765`, `bypass` / `force` /
`skip_rules` can never produce a silent allow on either the gate or the LAN
firewall, and the keyvault snapshot still declares OpenVault the source.

---

## Auto-ship + OpenVault HTTP (2026-08-25)

OpenVault detects the stack and ships HTTP itself. Cursor Origin is git-only.
Vercel is not used. Runtime is Caddy (load balancer + Let's Encrypt) plus
systemd on Hetzner / any VPS / AWS (SSM restart). Default target: `vps_ssh`.

| Kind | Examples | Git | HTTP runtime | Auto-update |
|------|----------|-----|--------------|-------------|
| `static_http` | Vite, Astro, `index.html` | Origin (optional) | Caddy `file_server` | git pull + Caddy reload; GET /healthz |
| `edge_http` | Next.js, Hono | Origin (optional) | systemd (`next start`) + Caddy reverse_proxy | git pull + systemctl restart |
| `process` | FastAPI, Django, Flask, Node, Go, Rust | Origin (optional) | systemd + Caddy | same |
| `container` | Dockerfile / compose | Origin (optional) | compose on the VM + Caddy | same |

| Route | Purpose |
|-------|---------|
| `POST /api/detect` | Stack + commands + `host_kind` (absolute path only) |
| `GET /api/ship/stacks` | Catalog (ports, commands, Origin HTTP yes/no) |
| `POST /api/ship/ready` | Ready-to-ship gates (detect, commands, domain, Caddy runtime) |
| `POST /api/ship/auto` | Origin git plan + Caddy/systemd server plan + CI workflow |
| `POST /api/ship/server` | Caddyfile + systemd unit + health + (AWS) SSM |
| `POST /api/ship/cicd/plan` | GitHub Actions that scp, restart systemd, curl /healthz |
| `GET /api/ship/origin/status` | `origin` CLI / `ORIGIN_MODE=simulate` |

`vercel.json` is still parsed as a **detect hint** (rootDirectory / buildCommand)
and is otherwise ignored. `origin_vercel_ready` is always false.
`ORIGIN_MODE=simulate` is the default when the Origin CLI is missing.
Live apply uses `OPENVAULT_SHIP_MODE=live` plus `vps_host` / `OPENVAULT_VPS_HOST`.

---

## Next priorities

1. Redis + Lua `BucketStore` so a cluster of gateways cannot double-spend a budget.
2. Streaming `/v1/chat/completions` on top of the existing reserve/refund path.
3. Small Software cloud UI tab in OpenVault console (devices + shares + live sessions).
4. Live observe path from real admin timings + wear PRE-FLIGHT.
5. `training_router.py` + wire `openmw train`.
6. Control writes only after hardware-proven capability (see PARKINGLOT).

---

## Clone-and-verify

```bash
uv sync && uv run pytest tests/ -q && uv run mypy nvme_sentinel tests
cd OpenMW && uv sync && uv run pytest tests/ -q && uv run mypy openmw
uv run python -c "from openmw.openvault.app import create_app; create_app(mock_health=True)"
uv run openmw doctor -o /tmp/doctor_check
```
