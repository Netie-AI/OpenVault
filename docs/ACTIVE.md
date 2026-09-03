# Active map

What exists in this repo and where. Update when structure changes, not when state changes
- state lives in STATUS.md.

## Layout

| Path | What |
|------|------|
| `nvme_sentinel/` | NVMe HAL, SMART, BenchRunReport (library, own `uv sync` root) |
| `Profiler/` | PathTrace + capability probe (library, own `uv sync` root) |
| `OpenMW/openmw/openvault/` | App tiers (own `uv sync` root) — see table below |
| `OpenMW/` | Custody API on `:5000` (redirects `/` to the app) |
| `OpenMW/rust/openvault-console/` | Optional Rust auth on `:5055`, skipped when `cargo` missing |
| `apps/web/` | OpenVault UI on `:3010` (Next 16, wired exclusively to `:5000` — see `docs/decisions/DR-0003-openship-app-plan.md`) |
| `apps/cli/` | `openvault_cli.py` — `up` / `demo` / `demo-path` / `app` / `doctor` |
| `OpenMW/scripts/one_seat_demo.py` | Auto-safe one-seat evidence path (vault → FreeRoute refuse → ship allow → deny); see [`ONE_SEAT_DEMO.md`](ONE_SEAT_DEMO.md) |
| `apps/shell/` | Electron desktop shell |
| `docs/reference/` | Protocol/technical reference docs still live and current |
| `docs/decisions/` | Decision records, `DR-####-kebab-title.md`, MADR format (see `DR-0001`). Proposed: [`DR-0012`](decisions/DR-0012-skills-kb-crew-wiring.md) skills/KB/crew wiring |
| `scripts/windows/` | `Start-OpenVaultDemo.ps1`, `Start-NetieStack.ps1`, `Start-LocalMesh.ps1` |
| `scripts/` | `start_local_mesh.sh` (Linux/macOS mesh bring-up: custody API `:5000`, approve Cortex + OpenIDE, demo shell when AirGPT is absent) · `airgpt_demo_shell.py` (stdlib AirGPT/OpenIDE stand-in on `:8765`) |
| `bin/` | Quarantine — dead/orphaned files pending the founder's final review and removal |

## OpenVault app tiers (`OpenMW/openmw/openvault/`)

| Tier | Package | Role |
|------|---------|------|
| Health | `health/` | Laptop device inventory |
| Observe | `observe/` | PathTrace hops + severity (`hot` = red) |
| Vault | `vault/` | Encrypted keys, accounts, proxy, FreeRoute gateway. Metering trio: `api_keys.py` (issued `ov_` credentials) · `auth.py` (who is calling — never a header) · `usage_store.py` (one durable row per request) · `budget.py` (output ceiling + context refusal) |
| Ship | `ship/` (+ `ship/hosts/`) | Deploy / FreeBuild / email gates |
| Mesh | `mesh/` | Local mesh + Cortex client + `/api/slots` |
| Control | `control/` | GPU/CPU/fan remediation (dry_run default) |
| Cloud | `cloud/` | Small Software LAN cloud — device discovery, shares, multiplayer sessions (`docs/decisions/DR-0002-small-software-lan-cloud.md`) |
| Route | `route/` | Attempt classifier, park/kill-and-send, fallback chain |
| Routers | `routers/` | New-style `APIRouter` surfaces (health history, etc.) — replacing the single-file `app.py` route style over time |
| Sentinel | `sentinel/` | NVMe Sentinel engine binding (`nvme_sentinel` + `Profiler` into the app) |

Peer (not in this repo): Cortex at `D:\Cortex` -> `http://127.0.0.1:8000` (URL wiring only).

## Quickstart

```bash
uv sync && uv run nvme-sentinel demo
uv run mypy nvme_sentinel && uv run pytest tests/unit tests/integration -q
```

```bash
cd OpenMW && uv sync
uv run openmw console --cortex-url http://127.0.0.1:8000 --openide-url http://127.0.0.1:8765
```

Local mesh, Netie one-click stack, and API tables: see [`STATUS.md`](../STATUS.md).
Buyer one-seat demo (mocks only): [`ONE_SEAT_DEMO.md`](ONE_SEAT_DEMO.md).
Setup detail: [`setup.md`](setup.md). Architecture diagram: [`architecture.puml`](architecture.puml).
