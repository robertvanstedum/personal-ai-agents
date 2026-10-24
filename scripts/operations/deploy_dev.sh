#!/usr/bin/env bash
# Deploy one service at a pinned commit to dev (the Mac), or roll it back. Dev only; never production.
#   deploy_dev.sh deploy <service> <commit> --operator <name> [--overlay] [--dry-run] [...]
#   deploy_dev.sh rollback <service> --operator <name> [--to <record dir>]
#   deploy_dev.sh status [<service>]
# Design and rules: docs/DEV_DEPLOY_DESIGN_v0.1_2026-10-24.md. Logic: dev_deploy.py.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HEAVY_JOB="${MINIMOI_HEAVY_JOB_TOOL:-$HOME/Projects/personal-ai-agents/_working/tools/heavy_job.py}"
PY="${PYTHON:-python3}"

cmd="${1:-}"
case "$cmd" in
  status) exec "$PY" "$HERE/dev_deploy.py" "$@" ;;
  deploy|rollback) ;;
  *) sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac

# A dry run changes nothing, so it does not take the host-wide heavy-job lock.
for arg in "$@"; do
  [ "$arg" = "--dry-run" ] && exec "$PY" "$HERE/dev_deploy.py" "$@"
done

operator=""
prev=""
for arg in "$@"; do
  [ "$prev" = "--operator" ] && operator="$arg"
  prev="$arg"
done
[ -n "$operator" ] || { echo "refused: --operator <name> is required" >&2; exit 2; }
[ -f "$HEAVY_JOB" ] || { echo "refused: heavy-job tool not found at $HEAVY_JOB" >&2; exit 2; }

exec "$PY" "$HEAVY_JOB" "$operator" "dev-$cmd" -- env MINIMOI_HEAVY_JOB=1 "$PY" "$HERE/dev_deploy.py" "$@"
