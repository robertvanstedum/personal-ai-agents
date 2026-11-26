#!/usr/bin/env bash
# One automatic UAT run against staging (SPEC_AUTOMATIC_UAT v0.1 §3 and §5). Runs on the staging host.
#
#   scripts/uat/run_staging_uat.sh [extra staging_uat.py args, e.g. --only A1,B2]
#
# * The runner runs inside the official Playwright Python image (pinned below), with the repo mounted read-only and
#   only the run's output folder writable.
# * The sign-in token comes from SSM (/minimoi/staging/uat_signin_token, SecureString). It is passed to the container
#   as an environment variable by NAME only (docker run -e MINIMOI_UAT_TOKEN), so it never appears on a command line,
#   in `ps`, or in this script's output.
# * Output: /opt/minimoi-staging/uat/<UTC run id>/ (report.md, report.json, screenshots/, runner.log, exit_code).
# * Exit code: the runner's (0 green, 1 a case failed or errored, 2 configuration), 75 if another run holds the lock.
set -euo pipefail
set +x                                                       # never trace: the token lives in this shell

IMAGE="${UAT_IMAGE:-mcr.microsoft.com/playwright/python:v1.49.1-noble}"
UAT_ROOT="${UAT_ROOT:-/opt/minimoi-staging/uat}"
SSM_NAME="${UAT_SSM_NAME:-/minimoi/staging/uat_signin_token}"
BASE_URL="${UAT_BASE_URL:-https://dev.minimoi.ai}"
REPO_DIR="${UAT_REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
RUN_ID="${UAT_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
LOCK="${UAT_LOCK:-$UAT_ROOT/.uat.lock}"

case "$BASE_URL" in
  https://dev.minimoi.ai|https://dev.minimoi.ai/) ;;
  *) echo "refusing: UAT runs against staging (https://dev.minimoi.ai) only, not $BASE_URL" >&2; exit 2 ;;
esac

mkdir -p "$UAT_ROOT"
# The watcher already holds the lock (UAT_LOCK_HELD=1); a hand run takes it here so two runs never overlap.
if [ "${UAT_LOCK_HELD:-0}" != "1" ]; then
  exec 9>"$LOCK"
  if ! flock -n 9; then echo "another UAT run holds $LOCK" >&2; exit 75; fi
fi

OUT="$UAT_ROOT/$RUN_ID"
mkdir -p "$OUT"

MINIMOI_UAT_TOKEN="$(aws ssm get-parameter --name "$SSM_NAME" --with-decryption \
  --query Parameter.Value --output text 2>/dev/null)" || { echo "could not read the UAT token from SSM" >&2; exit 2; }
if [ -z "$MINIMOI_UAT_TOKEN" ] || [ "$MINIMOI_UAT_TOKEN" = "None" ]; then
  echo "the UAT token in SSM is empty" >&2; exit 2
fi
export MINIMOI_UAT_TOKEN

echo "UAT run $RUN_ID → $OUT (image $IMAGE, repo $REPO_DIR @ $(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || echo '?'))"

set +e
docker run --rm --init --ipc=host \
  --name "minimoi-uat-$RUN_ID" \
  --user "$(id -u):$(id -g)" \
  -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 -e UAT_RUN_ID="$RUN_ID" \
  -e MINIMOI_UAT_TOKEN \
  -v "$REPO_DIR:/repo:ro" \
  -v "$OUT:/out" \
  -w /tmp \
  "$IMAGE" \
  python3 /repo/tests/uat/staging_uat.py --base-url "$BASE_URL" --token-env MINIMOI_UAT_TOKEN \
    --run-id "$RUN_ID" --out /out "$@" 2>&1 | tee "$OUT/runner.log"
rc=${PIPESTATUS[0]}
set -e
unset MINIMOI_UAT_TOKEN

echo "$rc" > "$OUT/exit_code"
echo "UAT run $RUN_ID finished: exit $rc, report $OUT/report.md"
exit "$rc"
