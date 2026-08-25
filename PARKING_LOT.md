# OpenVault — Parking lot

> Deferred / research backlog. Not sequenced for near-term coding unless promoted into [`STATUS.md`](STATUS.md).

---

## Engineering backlog

| Item | Notes |
|------|-------|
| Netie `user.env` DPAPI wrap | Out of this tree — `EnvLoader` fix lives in `D:\Netie Space`. Unlock: someone picks it up in that repo. |
| ~~SSH VPS executor~~ | Done 2026-08-06 — `ship/hosts/vps_ssh.py` (Docker + Caddy, replicas, blue/green, TLS). |
| Multi-node load balancing | Today one box runs both the app and its proxy, so the box is the single point of failure. Unlock: a user hits the ceiling of one VPS, or asks for HA. Needs a real LB tier (second node + floating IP or DNS round-robin) and a shared session/store story. |
| Route53 / registrar DNS automation | We hand back the exact A record and verify resolution; we never create it. Unlock: the founder decides OpenVault may hold a domain-registrar credential — today that is custody the user keeps. |
| Rollback command for `vps_ssh` | The pieces exist (last 3 static releases kept, last 3 images kept, previous colour known) but there is no one-button rollback. Unlock: first user who needs to undo a bad deploy. |
| Vaulted SSH key material | `from_vault` deliberately takes a key *path*, not key bytes — writing a private key to disk each deploy would undo the custody epic. Unlock: an agent-forward or in-memory key path that never touches disk. |
| Response cache (idempotency) | Designed, not built. OmniRoute's "semantic cache" has no embeddings — it is a SHA-256 exact match that only fires on explicit `temperature: 0`, so it rarely triggers. Ours would key on the issued API key so tenants cannot read each other's responses, and record `cache_hit` in the ledger (the column already exists). Unlock: someone wants it, or a tenant's traffic shows real repeat rate. |
| Verified provider context-window table | `ProviderSpec.context_window` exists and defaults to 0 = unknown = never refuse; `OPENVAULT_CONTEXT_WINDOWS` is the operator escape hatch. Numbers were **not** invented — the catalog's own discipline is "Pinned from…/Verified against…". Unlock: someone pulls each provider's published limits with a citation. |
| ~~Per-tenant provider-key custody~~ | Unlocked and done 2026-08-19 — the founder picked pooled keys ([`DR-0009`](docs/decisions/DR-0009-pooled-key-custody.md)). `ordered_candidates` now walks pooled keys only; a `tenant`-custody key never enters the pool. Custody-as-a-service is the road not taken, and reopening it means a new decision record, not this entry. |
| Skills library / distill ingest / Cortex crew | OpenVault half shipped (`/api/crew/gate`, skill/mcp signposts, stripped indexes). Cortex registry + crew loop still not this repo — `Netie-AI/Cortex` is 404 with the cloud-agent token. Unlock: add that repo to the environment and grant clone/push, then implement Cortex `/api/skills` `/api/crew` `/api/mcp` against the gate. |
| Anthropic prompt-cache breakpoints | The single biggest cost lever upstream (cached input ≈ 10% of list), and unreachable: `proxy.py` skips anthropic hops entirely ("anthropic chat not via /v1 proxy yet"). Unlock: build the Anthropic Messages path first. |
| Multi-worker deploy lock | `project_deploy_lock` is per-process. Two uvicorn workers would each hold their own. Unlock: OpenVault runs with >1 worker — today it does not. |
| Native Win32 device enumeration | Replace PowerShell `Get-PhysicalDisk` spawn in discovery; `_windows_native.py` already proves the ctypes pattern |
| `collect --yes` / non-interactive elevate | Skip `Auto-elevate?` for scripted two-collect sequences |
| Timeout architecture polish | Align inner `subprocess.run(timeout=)` with outer probe budget; kill children on timeout (partially addressed; keep watching zombies) |
| Full laptop EC fan write | Control tier returns `capability: false` until a probe proves writable EC |
| Force GPU clocks / power limits | Scaffold only (`gpu.power_hint`); needs workstation-proven path |

---

## Observability / vision (post-P6)

| Capability | Priority |
|------------|----------|
| PCIe link health (`lspci` width/speed, AER) | High |
| Data-path hop timing (SSD → page cache → DRAM → PCIe → GPU) | Crown jewel — Full-Path Profiler |
| GPU util via NVML / `nvidia-smi` wrap | Integrate |
| CPU / RAM / ECC counters | Integrate |
| Training-step ↔ hardware telemetry overlay | Integrate |
| Inference KV-offload measurement (LMCache / vLLM + BenchRunReport) | Near-term research after wear PRE-FLIGHT |
| Training weight-tiering study (ZeRO-Infinity-style evidence vs vendor claims) | Later research |
| One-click Docker / PS1 install with capability degrade | Last |
| NVMeVirt lab for FTL experiments | Optional Linux research |

---

## Profiler / kernel acceleration (from PART 10 draft)

Open questions before more prompts:

1. Repo split — profiler lives in `Profiler/` vs nested under `nvme_sentinel/` (currently separate tree).
2. Workstation GPU for real GDS testing, or GeForce/laptop only (document “unsupported” with evidence).
3. Spare secondary NVMe for SPDK / wear-readable passthrough (not boot drive).
4. Profiler v1 OS priority — Linux (Nsight + io_uring) vs Windows (IoRing/DirectStorage spike).
5. Predictive prefetch bar — config/demo vs novel algorithm.

Honest constraints already decided:

- GDS not available on commodity GeForce laptops.
- SPDK cannot unbind a laptop boot NVMe.
- Realistic levers: Linux `io_uring` passthrough; Windows IoRing/DirectStorage as exploratory spike.
- Capability probe must auto-detect and degrade (same honesty as WMI degraded-telemetry banner).

---

## Hardware / lab notes (not bugs)

- Windows `stornvme.sys` STOPPED → passthrough `ERROR_INVALID_FUNCTION`; WMI fallback is correct behavior.
- USB-bridged NVMe: WMI does not expose NVMe Data Units Written → wear delta zero is honest.
- Boot drive: do not run endurance workloads; WSL2 sharing issues possible.
- Docker on Windows: Linux ioctl works in container, but real `/dev/nvme*` passthrough needs Linux host + spare drive.
