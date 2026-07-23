# OpenMW

Open measurement workspace for flash KV-cache offload (LMCache/vLLM) correlated with
nvme-sentinel wear accounting and nvme-profiler path traces.

## Quick start (mock / CI)

```bash
cd OpenMW
uv sync
uv run pytest
bash scripts/run_offload_demo.sh
```

## Layout

| Module | Purpose |
|--------|---------|
| `run.py` | snapshot → workload → snapshot → BenchRunReport + PathTraceReport |
| `prefetch_naive.py` | Phase-1 sequential prefetch config |
| `prefetch_heuristic.py` | Phase-2 heuristic overlay (research) |
| `windows_ioring_spike.py` | Q4 exploratory IoRing probe |

Real vLLM/LMCache runs require Linux + GPU; mock loop proves report correlation without them.

## WebUI demo (liquid glass dashboard)

```bash
cd OpenMW
uv sync
uv run openmw demo-ui --port 5000
```

Opens `http://127.0.0.1:5000/` with Detection · Data Flow · Bottleneck · Middleware Gain tabs.
Use `--mock-profile` for a fixed RTX 4050 demo profile, or `--no-serve` to only write `demo.json` + `index.html`.

## OpenVault console (secure keys + Cortex)

```bash
cd OpenMW
uv sync
uv run openmw console --port 5000 --mock-health
```

Starts the liquid-glass **OpenVault** console with:

- Continuous API-key precheck + primary→backup→cheap→free fallback
- Encrypted vault under `~/.openvault` (override with `OPENVAULT_HOME`)
- Secure local endpoint `http://127.0.0.1:5000/v1/chat/completions`
- Cortex / Netie Engine status + model catalog selection
- Hardware detection / bottleneck panels

Point Cursor / tools at the `/v1` endpoint. Cortex URL defaults to `http://127.0.0.1:8000` (`--cortex-url`).

See [`docs/openvault-cortex.md`](../docs/openvault-cortex.md).
Scale-merge map (Cortex deploy-to-web → OpenShip gates): [`docs/OPENVAULT_SCALE_MERGE.md`](../docs/OPENVAULT_SCALE_MERGE.md).
