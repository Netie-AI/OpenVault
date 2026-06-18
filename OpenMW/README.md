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
