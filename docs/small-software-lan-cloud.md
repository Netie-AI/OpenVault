# Small Software LAN Cloud

Goal: make purpose-built team apps as easy to share as a Google Doc — on the **company LAN**, not AWS/Azure.

Inspired by Pete Koomen (*A Cloud for Small Software*) and Aaron Epstein (*Multiplayer AI*).

## Shape (v0)

```
OpenIDE (create) ──share──▶ OpenVault cloud registry
       ▲                         │
       │                    firewall / extruder
       │                         │
LAN peers ◀── pull by share code ─┘
Multiplayer session: join same live agent thread
```

## APIs

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/cloud/devices` | Detect LAN devices (ARP + self) |
| GET | `/api/cloud/rules` | Published firewall rules |
| POST | `/api/cloud/firewall/check` | Allow/deny (bypass → WARN + cannot) |
| POST | `/api/cloud/shares` | Publish app metadata (no secrets) |
| GET | `/api/cloud/shares` | List shares |
| POST | `/api/cloud/sessions` | Create multiplayer agent session |
| POST | `/api/cloud/sessions/{id}/join` | Join live session |

## Rules (hard)

1. Private LAN / loopback only — no public internet share.
2. Secrets never leave the vault; `env_edge` strips KEY/TOKEN/SECRET.
3. `bypass` / `force` / `skip_rules` → **WARN + deny** (cannot).
4. Deploy / leave-machine still require `/api/gate/check`.
5. Peers may pull apps; they cannot remote-exec on this host.

## Stress

```bash
python scripts/stress_four_mesh_playwright.py
cd OpenMW && uv run pytest tests/test_small_cloud.py -q
```
