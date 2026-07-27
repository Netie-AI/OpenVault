#!/usr/bin/env bash
# Linux/macOS local mesh: OpenVault :5000 + optional AirGPT stub :8765.
# Cortex must already be listening on :8000 (or set CORTEX_URL).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CORTEX_URL="${CORTEX_URL:-http://127.0.0.1:8000}"
OPENIDE_URL="${OPENIDE_URL:-http://127.0.0.1:8765}"
OPENVAULT_HOME="${OPENVAULT_HOME:-$ROOT/.openvault}"
WITH_AIRGPT_STUB="${WITH_AIRGPT_STUB:-1}"
SKIP_BROWSER="${SKIP_BROWSER:-1}"
LOG_DIR="${LOG_DIR:-$ROOT/.openvault/logs}"

mkdir -p "$OPENVAULT_HOME" "$LOG_DIR"
export OPENVAULT_HOME CORTEX_URL OPENIDE_URL
export PATH="${HOME}/.local/bin:${PATH}"

cd "$ROOT/OpenMW"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="${HOME}/.local/bin:${PATH}"
fi
uv sync

OV_ARGS=(run openmw console --host 127.0.0.1 --port 5000
  --cortex-url "$CORTEX_URL" --openide-url "$OPENIDE_URL" --mock-health)
if [[ "$SKIP_BROWSER" == "1" ]]; then
  OV_ARGS+=(--no-open-browser)
fi

echo "==> Starting OpenVault on :5000"
nohup uv "${OV_ARGS[@]}" >"$LOG_DIR/openvault.out.log" 2>"$LOG_DIR/openvault.err.log" &
echo $! >"$LOG_DIR/openvault.pid"

ok=0
for _ in $(seq 1 40); do
  if curl -sf "http://127.0.0.1:5000/api/healthz" >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 1
done
if [[ "$ok" != "1" ]]; then
  echo "OpenVault failed to become healthy; see $LOG_DIR/openvault.err.log" >&2
  tail -n 40 "$LOG_DIR/openvault.err.log" >&2 || true
  exit 1
fi

echo "==> Approving Cortex + OpenIDE on mesh"
curl -sf -X PUT "http://127.0.0.1:5000/api/local/mesh/config" \
  -H 'Content-Type: application/json' \
  -d "{\"auto_approve_loopback\":true,\"cortex_url\":\"$CORTEX_URL\",\"openide_url\":\"$OPENIDE_URL\",\"rust_console_url\":\"http://127.0.0.1:5055\"}" >/dev/null

curl -sf -X POST "http://127.0.0.1:5000/api/local/handshake" \
  -H 'Content-Type: application/json' \
  -d "{\"peer_kind\":\"cortex\",\"name\":\"Cortex Netie Engine\",\"base_url\":\"$CORTEX_URL\",\"capabilities\":[\"engines\",\"models\",\"deploy\"],\"auto_approve\":true}" >/dev/null

curl -sf -X POST "http://127.0.0.1:5000/api/local/handshake" \
  -H 'Content-Type: application/json' \
  -d "{\"peer_kind\":\"openide\",\"name\":\"AirGPT OpenIDE\",\"base_url\":\"$OPENIDE_URL\",\"capabilities\":[\"signin\",\"passkey\",\"editor\"],\"auto_approve\":true}" >/dev/null

curl -sf "http://127.0.0.1:5000/api/local/connect-pack" >"$OPENVAULT_HOME/connect_pack.json"

if [[ "$WITH_AIRGPT_STUB" == "1" ]]; then
  if ! curl -sf "$OPENIDE_URL/" >/dev/null 2>&1; then
    echo "==> Starting AirGPT demo shell on :8765 (real AirGPT not detected)"
    nohup python3 "$ROOT/scripts/airgpt_demo_shell.py" \
      >"$LOG_DIR/airgpt.out.log" 2>"$LOG_DIR/airgpt.err.log" &
    echo $! >"$LOG_DIR/airgpt.pid"
    for _ in $(seq 1 20); do
      curl -sf "$OPENIDE_URL/" >/dev/null 2>&1 && break
      sleep 0.5
    done
  else
    echo "==> AirGPT/OpenIDE already up at $OPENIDE_URL"
  fi
fi

echo ""
echo "OpenVault UI:  http://127.0.0.1:5000/#mesh"
echo "AirGPT/IDE:    $OPENIDE_URL"
echo "Cortex:        $CORTEX_URL"
echo "Connect pack:  $OPENVAULT_HOME/connect_pack.json"
