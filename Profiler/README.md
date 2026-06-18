# nvme-profiler

Full-path bottleneck profiler and capability probe for the nvme-sentinel workspace.

## Quick start

```bash
cd Profiler
uv sync
uv run nvme-profiler probe --out capability_manifest.json
uv run pytest
```

From repo root:

```bash
make probe
```

## Packages

| Module | Purpose |
|--------|---------|
| `probe.py` | Host capability detection → `CapabilityManifest` |
| `schema.py` | `PathTraceReport`, hop records, acceleration tiers |
| `fuse.py` | Fuse SSD `_timed()` records with nsys timeline |
| `report.py` | Dark-industrial HTML for path traces |

Depends on `nvme-sentinel` (path dependency) for device inventory and SSD timing ground truth.
