---
status: accepted
date: 2026-07-23
decision-makers: Claude, founder
---

# DR-0002 - Small Software LAN Cloud

## Context and Problem Statement

OpenVault needed a story for sharing purpose-built team apps without adopting a public
cloud. Team apps should be as easy to hand a peer as a Google Doc, but keys and app
artifacts must never leave the machine's custody boundary.

## Considered Options

- Public cloud (AWS/Azure) hosting and sharing
- No sharing model — keep OpenVault single-machine only
- LAN-first sharing: local device discovery, share codes, deny-by-default firewall rules, no secrets on the wire

## Decision Outcome

Chosen option: "LAN-first sharing," because it matches OpenVault's local-first custody
model (see [`PRODUCT_ROLES.md`](../../PRODUCT_ROLES.md)) without standing up cloud
infrastructure or a new trust boundary. Private LAN/loopback only; `bypass`/`force`/
`skip_rules` always resolve to WARN + deny; peers can pull apps but never remote-exec.

## Consequences

- Good: no cloud bill or public attack surface; secrets never leave the vault (`env_edge`
  strips KEY/TOKEN/SECRET before any share).
- Bad: no off-LAN collaboration without a VPN; multiplayer sessions require peers already
  on the same network.

## Confirmation

`OpenMW/tests/test_small_cloud.py` (exists) plus manual multi-tab stress via
`scripts/stress_four_mesh_playwright.py`.

---

## Original record (archived 2026-08-02, body preserved as-is)

# Small Software LAN Cloud

Goal: make purpose-built team apps as easy to share as a Google Doc — on the **company LAN**, not AWS/Azure.

Inspired by Pete Koomen (*A Cloud for Small Software*) and Aaron Epstein (*Multiplayer AI*).

## Shape (v0)

```
FreeIDE (create) ──share──▶ OpenVault cloud registry
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
