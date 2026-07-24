# OpenVault — Status

> Canonical “what’s done / what’s next.” Deferred ideas: [`PARKINGLOT.md`](PARKINGLOT.md).
> Protocol/architecture: [`implementation_plan.md`](implementation_plan.md).

Last reconciled: 2026-07-23.

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

Legacy: `GET /api/health/bottleneck` still works (aliases observe).

---

## Local mesh runbook

Ports: OpenVault `5000` · Rust auth `5055` · Cortex `8000` · OpenIDE `5100`.

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

## Next priorities

1. Small Software cloud UI tab in OpenVault console (devices + shares + live sessions).
2. Live observe path from real admin timings + wear PRE-FLIGHT.
3. `training_router.py` + wire `openmw train`.
4. Control writes only after hardware-proven capability (see PARKINGLOT).

---

## Clone-and-verify

```bash
uv sync && uv run pytest tests/ -q && uv run mypy nvme_sentinel tests
cd OpenMW && uv sync && uv run pytest tests/ -q && uv run mypy openmw
uv run python -c "from openmw.openvault.app import create_app; create_app(mock_health=True)"
uv run openmw doctor -o /tmp/doctor_check
```
