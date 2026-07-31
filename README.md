# OpenVault

> One local control plane: **see bottleneck → acknowledge model slots → hold keys → ship → mesh into Cortex → gated fix**.

**Product roles:** OpenVault = keys + gate + connect + deploy/host. Cortex = brains. AirGPT = shell. FreeIDE = coding. See [`PRODUCT_ROLES.md`](PRODUCT_ROLES.md) — do not grow a second key vault or a third orchestrator.

Libraries for measurement stay at the repo root; the operator console lives in OpenMW.

---

## Product flow

1. **See** — NVMe → PCIe → DRAM → VRAM → GPU path with red hotspots (`/api/observe/path`, Bottleneck tab)
2. **Slots** — every local + Cortex model acknowledged (`/api/slots`)
3. **Keys** — encrypted vault + fallback proxy
4. **Ship** — FreeBuild / deploy gates / email DNS checks
5. **Mesh** — OpenVault `:5000` ↔ Cortex `:8000` ↔ FreeIDE `:8765` ↔ Rust `:5055`
6. **Fix** — GPU/CPU/fan control with `dry_run` default (`/api/control/*`)

---

## Layout

| Path | What |
|------|------|
| `nvme_sentinel/` | NVMe HAL, SMART, BenchRunReport (library) |
| `Profiler/` | PathTrace + capability probe (library) |
| `OpenMW/openmw/openvault/` | App tiers: `health/` `observe/` `vault/` `ship/` `mesh/` `control/` |
| `OpenMW/` | Custody API on `:5000` (redirects `/` → app) |
| `apps/web/` | **OpenVault UI** on `:3010` |
| `docs/` | Setup, design decisions, architecture diagram |
| `scripts/windows/` | `Start-OpenVaultDemo.ps1`, `Start-NetieStack.ps1`, `Start-LocalMesh.ps1` |
| `openvault.local.example.json` | Copy to `openvault.local.json` |

Peer (not in this repo): Cortex at `D:\Cortex` → `http://127.0.0.1:8000` (URL wiring only).

Three separate `uv sync` roots today: repo root (sentinel), `OpenMW/`, `Profiler/`.

---

## nvme-sentinel quickstart

```bash
uv sync
uv run nvme-sentinel demo
```

```bash
uv run mypy nvme_sentinel
uv run pytest tests/unit tests/integration -q
```

---

## Local mesh

```
FreeIDE :8765  ──handshake──►  OpenVault :5000  ◄──engines──  Cortex :8000
                                  │
                                  └── passkeys ──► Rust :5055
```

1. Start Cortex on `:8000` yourself (e.g. from `D:\Cortex`).
2. `powershell -ExecutionPolicy Bypass -File scripts\windows\Start-LocalMesh.ps1` (optional `-WithRustAuth`).
3. Open http://127.0.0.1:5000/#mesh and approve peers.
4. Connect pack: `.openvault/connect_pack.json`.

```bash
cd OpenMW && uv sync
uv run openmw console --cortex-url http://127.0.0.1:8000 --openide-url http://127.0.0.1:8765
```

---

## Documentation

- Current state: [`STATUS.md`](STATUS.md)
- Deferred backlog: [`PARKINGLOT.md`](PARKINGLOT.md)
- Setup: [`docs/setup.md`](docs/setup.md)
- Design: [`docs/design-decisions.md`](docs/design-decisions.md)
- Architecture: [`docs/architecture.puml`](docs/architecture.puml)
- Agent protocol plan: [`implementation_plan.md`](implementation_plan.md)
- Subprojects: [`OpenMW/README.md`](OpenMW/README.md), [`Profiler/README.md`](Profiler/README.md)

---

## License

MIT
