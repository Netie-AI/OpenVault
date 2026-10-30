#!/usr/bin/env bash
# bin/rollback.sh: roll FreeRoute back after a bad deploy. Part of the
# deploy-rollback incident-recovery flow.
#
# FreeRoute is launched by OpenVault, so its code version is the OpenVault
# release that is checked out. There is no separate package to install over it
# (no `npm install -g`, no registry image), so this script does not install
# anything. It does two things:
#   • --data <snapshot-id|path> restores FreeRoute's SQLite data from a snapshot
#     made by bin/snapshot-data.sh, by calling bin/restore-data.sh.
#   • always prints the steps that move the code back: check out the previous
#     OpenVault release and restart the console, which relaunches FreeRoute.
# Provider keys are not part of FreeRoute's data. They stay in OpenVault.
set -euo pipefail
SCRIPT_NAME="rollback"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_ops-common.sh"

usage() {
  cat <<'USAGE'
Usage: bin/rollback.sh [--data <snapshot-id|path>] [--data-dir <path>] [--yes|-y] [-h|--help]

Rolls FreeRoute back. FreeRoute is launched by OpenVault, so the code moves back
with the OpenVault checkout: this script prints those steps and, with --data,
restores FreeRoute's SQLite data from a snapshot (see bin/snapshot-data.sh).
Nothing is installed from a package registry.
USAGE
}

SNAPSHOT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --yes | -y) ASSUME_YES=1; shift ;;
    --data) SNAPSHOT="${2:?--data needs a snapshot id or path}"; shift 2 ;;
    --data-dir) ops_set_data_dir "${2:?--data-dir needs a value}"; shift 2 ;;
    -h | --help) usage; exit 0 ;;
    -*) ops_die "unknown argument: $1 (see --help)" ;;
    *) ops_die "unexpected argument: $1 (FreeRoute is not rolled back by version; see --help)" ;;
  esac
done

ops_log "code: check out the previous OpenVault release (git checkout <tag>), then restart the OpenVault console. It relaunches FreeRoute from that checkout."

if [ -n "$SNAPSHOT" ]; then
  restore_args=("$SNAPSHOT" --data-dir "$OMNIROUTE_DATA_DIR")
  if [ "${ASSUME_YES:-0}" = "1" ]; then restore_args+=(--yes); fi
  ops_log "data: restoring FreeRoute data from snapshot $SNAPSHOT (stop FreeRoute first)"
  "$SCRIPT_DIR/restore-data.sh" "${restore_args[@]}"
else
  ops_log "data: unchanged (pass --data <snapshot-id> to restore a snapshot)"
fi
ops_log "rollback steps complete"
