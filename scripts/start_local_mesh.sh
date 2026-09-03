#!/usr/bin/env bash
# Linux/macOS local mesh bring-up for full demos.
#
#   OpenVault custody API  :5000  started here, same argv as apps/cli/openvault_cli.py
#   AirGPT / OpenIDE       :8765  real AirGPT if already up, else scripts/airgpt_demo_shell.py
#   Cortex                 :8000  must already be running (Cortex lives in D:\Cortex)
#
# Windows equivalent: scripts/windows/Start-LocalMesh.ps1. The OpenVault web app
# (:3010) is not started here - use `python apps/cli/openvault_cli.py up` for that.
#
# Environment overrides:
#   OPENVAULT_HOME      vault home (default <repo>/.openvault, same as the launchers)
#   OPENVAULT_API_PORT  custody API port (else $OPENVAULT_HOME/ports.json, else 5000)
#   CORTEX_URL          default http://127.0.0.1:8000
#   OPENIDE_URL         default http://127.0.0.1:8765
#   OPENVAULT_RUST_URL  default http://127.0.0.1:5055 (reported on the mesh, not started)
#   MOCK_HEALTH=0       real device health instead of --mock-health (default 1: demo)
#   WITH_AIRGPT_STUB=0  never start the demo shell, even when nothing answers on OPENIDE_URL
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OPENMW="$ROOT/OpenMW"
OPENVAULT_HOME="${OPENVAULT_HOME:-$ROOT/.openvault}"
CORTEX_URL="${CORTEX_URL:-http://127.0.0.1:8000}"
OPENIDE_URL="${OPENIDE_URL:-http://127.0.0.1:8765}"
OPENIDE_URL="${OPENIDE_URL%/}"
RUST_CONSOLE_URL="${OPENVAULT_RUST_URL:-http://127.0.0.1:5055}"
MOCK_HEALTH="${MOCK_HEALTH:-1}"
WITH_AIRGPT_STUB="${WITH_AIRGPT_STUB:-1}"
LOG_DIR="$OPENVAULT_HOME/logs"

mkdir -p "$OPENVAULT_HOME" "$LOG_DIR"
export OPENVAULT_HOME CORTEX_URL OPENIDE_URL
export OPENVAULT_RUST_URL="$RUST_CONSOLE_URL"
export PATH="${HOME}/.local/bin:${PATH}"

if ! command -v uv >/dev/null 2>&1; then
  echo "==> Installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="${HOME}/.local/bin:${PATH}"
fi

cd "$OPENMW"
uv sync

# Port precedence matches openmw.openvault.ports.resolve_port and the CLI:
# environment > $OPENVAULT_HOME/ports.json > default.
API_PORT="${OPENVAULT_API_PORT:-}"
if [[ -z "$API_PORT" && -f "$OPENVAULT_HOME/ports.json" ]]; then
  API_PORT="$(uv run --no-sync python -c \
    'import json,sys; print((json.load(open(sys.argv[1])).get("ports") or {}).get("api") or "")' \
    "$OPENVAULT_HOME/ports.json" 2>/dev/null || true)"
fi
API_PORT="${API_PORT:-5000}"
OV_URL="http://127.0.0.1:$API_PORT"
export OPENVAULT_URL="$OV_URL"

# curl exit 7 is "connection refused"; anything else means a listener answered,
# even badly. A socket that accepts and hangs is still "something is there".
port_busy() {
  local rc=0
  curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$1/" || rc=$?
  [[ "$rc" -ne 7 ]]
}

start_api=1
if port_busy "$API_PORT"; then
  # Same rule as apps/cli/openvault_cli.py (_start_api): reuse our own server,
  # refuse a stranger's. `openmw ports` names the blocking application and
  # exits 1 when a foreign process holds an OpenVault port (DR-0011).
  echo "==> Something is listening on :$API_PORT - asking openmw ports who"
  if ! uv run --no-sync openmw ports; then
    exit 1
  fi
  echo "==> Custody API already listening on :$API_PORT - reusing it."
  start_api=0
fi

if [[ "$start_api" == "1" ]]; then
  OV_ARGS=(run --no-sync openmw console --host 127.0.0.1 --port "$API_PORT"
    --cortex-url "$CORTEX_URL" --openide-url "$OPENIDE_URL" --no-open-browser)
  if [[ "$MOCK_HEALTH" == "1" ]]; then
    OV_ARGS+=(--mock-health)
  fi
  echo "==> Starting OpenVault custody API on :$API_PORT (home=$OPENVAULT_HOME)"
  echo "    log -> $LOG_DIR/console.up.log"
  nohup uv "${OV_ARGS[@]}" >"$LOG_DIR/console.up.log" 2>&1 &
  echo $! >"$LOG_DIR/console.pid"
fi

echo "==> Waiting for $OV_URL/api/healthz"
ok=0
for _ in $(seq 1 90); do
  if curl -sf "$OV_URL/api/healthz" >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 1
done
if [[ "$ok" != "1" ]]; then
  echo "OpenVault did not become healthy on :$API_PORT; see $LOG_DIR/console.up.log" >&2
  tail -n 40 "$LOG_DIR/console.up.log" >&2 || true
  exit 1
fi

echo "==> Approving Cortex + OpenIDE on the mesh"
curl -sf -X PUT "$OV_URL/api/local/mesh/config" \
  -H 'Content-Type: application/json' \
  -d "{\"auto_approve_loopback\":true,\"cortex_url\":\"$CORTEX_URL\",\"openide_url\":\"$OPENIDE_URL\",\"rust_console_url\":\"$RUST_CONSOLE_URL\"}" >/dev/null

curl -sf -X POST "$OV_URL/api/local/handshake" \
  -H 'Content-Type: application/json' \
  -d "{\"peer_kind\":\"cortex\",\"name\":\"Cortex Netie Engine\",\"base_url\":\"$CORTEX_URL\",\"capabilities\":[\"engines\",\"models\",\"deploy\"],\"auto_approve\":true}" >/dev/null

curl -sf -X POST "$OV_URL/api/local/handshake" \
  -H 'Content-Type: application/json' \
  -d "{\"peer_kind\":\"openide\",\"name\":\"AirGPT OpenIDE\",\"base_url\":\"$OPENIDE_URL\",\"capabilities\":[\"signin\",\"passkey\",\"editor\"],\"auto_approve\":true}" >/dev/null

curl -sf "$OV_URL/api/local/connect-pack" >"$OPENVAULT_HOME/connect_pack.json"

if [[ "$WITH_AIRGPT_STUB" == "1" ]]; then
  if curl -sf "$OPENIDE_URL/" >/dev/null 2>&1; then
    echo "==> AirGPT/OpenIDE already up at $OPENIDE_URL"
  else
    echo "==> Starting AirGPT demo shell at $OPENIDE_URL (real AirGPT not detected)"
    echo "    log -> $LOG_DIR/airgpt.log"
    read -r OPENIDE_HOST OPENIDE_PORT < <(uv run --no-sync python -c \
      'import sys, urllib.parse as u; p = u.urlparse(sys.argv[1]); print(p.hostname or "127.0.0.1", p.port or 8765)' \
      "$OPENIDE_URL")
    OPENIDE_HOST="$OPENIDE_HOST" OPENIDE_PORT="$OPENIDE_PORT" \
      nohup uv run --no-sync python "$ROOT/scripts/airgpt_demo_shell.py" \
      >"$LOG_DIR/airgpt.log" 2>&1 &
    echo $! >"$LOG_DIR/airgpt.pid"
    for _ in $(seq 1 30); do
      curl -sf "$OPENIDE_URL/health" >/dev/null 2>&1 && break
      sleep 0.5
    done
  fi
fi

PERFECT="$(uv run --no-sync python -c \
  'import json, sys; print(json.load(open(sys.argv[1]))["perfect_local"]["message"])' \
  "$OPENVAULT_HOME/connect_pack.json" 2>/dev/null || echo "unknown - read connect_pack.json")"

echo ""
echo "OpenVault API:   $OV_URL/   (mesh: $OV_URL/api/local/mesh)"
echo "OpenVault UI:    not started here - run: python apps/cli/openvault_cli.py up   (:3010)"
echo "AirGPT/OpenIDE:  $OPENIDE_URL"
echo "Cortex:          $CORTEX_URL"
echo "Connect pack:    $OPENVAULT_HOME/connect_pack.json"
echo "Perfect local:   $PERFECT"
echo "OpenIDE ready:   $OV_URL/api/openide/ready"
echo "Stop:            kill \$(cat $LOG_DIR/console.pid $LOG_DIR/airgpt.pid 2>/dev/null)"
