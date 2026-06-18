#!/usr/bin/env bash
# OpenMW offload demo (mock path — no vLLM/LMCache install required for CI)
set -euo pipefail
cd "$(dirname "$0")/.."
uv run python -c "
from pathlib import Path
from openmw.run import compare_prefetch_runs
out = Path('reports/openmw_demo')
cmp = compare_prefetch_runs(out)
print('Prefetch comparison:', cmp)
"
