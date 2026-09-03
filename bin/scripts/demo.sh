#!/usr/bin/env bash
# nvme-sentinel one-click Linux/Docker demo
# Usage: ./scripts/demo.sh [/dev/nvme0n1]
set -euo pipefail
DEVICE="${1:-}"
echo ""
echo "╔══════════════════════════════════════╗"
echo "║   nvme-sentinel Linux Demo           ║"
echo "╚══════════════════════════════════════╝"
echo ""
echo "[1/4] Hygiene checks..."
uv run ruff check . --quiet
uv run mypy nvme_sentinel --no-error-summary | grep -i error || true

echo "[2/4] Running test suite..."
uv run pytest tests/unit tests/integration -q --tb=short

echo "[3/4] Running live demo..."
if [ -n "$DEVICE" ]; then
    uv run nvme-sentinel smart --device "$DEVICE" --output reports/demo.html
else
    uv run nvme-sentinel demo
fi

echo "[4/4] Report location:"
REPORT="$(pwd)/reports/demo.html"
echo "  $REPORT"
if command -v xdg-open &>/dev/null; then xdg-open "$REPORT" 2>/dev/null || true; fi
echo ""
echo "Demo complete."
