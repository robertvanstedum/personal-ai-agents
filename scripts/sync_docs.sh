#!/bin/bash
# sync_docs.sh — push docs/build-queue from Mac to EC2 via AWS SSM.
#
# Replaces the SSH/rsync approach. Immune to Mac IP rotation because it uses
# ssm:SendCommand (same IAM credential as aws CLI already in use) — no SSH
# key, no EC2 security group rule, no fixed IP required.
#
# EC2 side pulls files from GitHub raw URLs pinned to the current commit.
# Files must be committed before running this script.
#
# Usage: ./scripts/sync_docs.sh
# Requires: aws CLI configured with minimoi-deploy credentials
#           (same credential used for ECR push and SSM parameter store)

set -euo pipefail

INSTANCE_ID="i-0d13db821169627e2"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GITHUB_REPO="robertvanstedum/personal-ai-agents"

# Guard: EC2 pulls from GitHub, not local disk — files must be committed.
DIRTY=$(git -C "$REPO_ROOT" diff --name-only HEAD -- \
  data/guild/build_queue.json docs/design/ docs/specs/ 2>/dev/null || true)
if [[ -n "$DIRTY" ]]; then
  echo "ERROR: uncommitted changes in synced paths — commit first, then run sync_docs.sh"
  echo "$DIRTY"
  exit 1
fi

COMMIT=$(git -C "$REPO_ROOT" rev-parse HEAD)
RAW_BASE="https://raw.githubusercontent.com/${GITHUB_REPO}/${COMMIT}"

echo "=== sync_docs.sh: SSM push (commit ${COMMIT:0:7}) ==="

# Build the list of (github-raw-url → ec2-dest-path) pairs
SRCS=()
DSTS=()

SRCS+=("${RAW_BASE}/data/guild/build_queue.json")
DSTS+=("/opt/minimoi/data/guild/build_queue.json")

for f in "${REPO_ROOT}"/docs/design/*; do
  [[ -f "$f" ]] || continue
  name=$(basename "$f")
  SRCS+=("${RAW_BASE}/docs/design/${name}")
  DSTS+=("/opt/minimoi/docs/design/${name}")
done

for f in "${REPO_ROOT}"/docs/specs/*; do
  [[ -f "$f" ]] || continue
  name=$(basename "$f")
  SRCS+=("${RAW_BASE}/docs/specs/${name}")
  DSTS+=("/opt/minimoi/docs/specs/${name}")
done

TOTAL=${#SRCS[@]}
echo "Files to sync: ${TOTAL}"

# Build a shell script to run on EC2 — one curl per file, fail-fast
LINES=("set -e")
for i in "${!SRCS[@]}"; do
  LINES+=("curl -fsSL '${SRCS[$i]}' -o '${DSTS[$i]}' && echo \"OK ($(( i + 1 ))/${TOTAL}): $(basename "${DSTS[$i]}")\"")
done
LINES+=("echo '=== done: ${TOTAL} files ==='")

# Encode as a JSON array of command lines (each line is a shell command)
CMD_JSON=$(python3 -c "
import json, sys
lines = sys.stdin.read().splitlines()
print(json.dumps(lines))
" <<< "$(printf '%s\n' "${LINES[@]}")")

CMD_ID=$(aws ssm send-command \
  --instance-ids "$INSTANCE_ID" \
  --document-name "AWS-RunShellScript" \
  --parameters "commands=${CMD_JSON}" \
  --query 'Command.CommandId' \
  --output text)

echo "SSM command: ${CMD_ID}"
echo -n "Waiting"

for _ in $(seq 1 30); do
  sleep 5
  echo -n "."
  STATUS=$(aws ssm get-command-invocation \
    --command-id "$CMD_ID" \
    --instance-id "$INSTANCE_ID" \
    --query 'Status' \
    --output text 2>/dev/null || echo "Pending")

  if [[ "$STATUS" == "Success" ]]; then
    echo ""
    aws ssm get-command-invocation \
      --command-id "$CMD_ID" \
      --instance-id "$INSTANCE_ID" \
      --query 'StandardOutputContent' \
      --output text
    exit 0
  elif [[ "$STATUS" == "Failed" || "$STATUS" == "Cancelled" ]]; then
    echo ""
    echo "=== FAILED (${STATUS}) ==="
    aws ssm get-command-invocation \
      --command-id "$CMD_ID" \
      --instance-id "$INSTANCE_ID" \
      --query '[StandardOutputContent, StandardErrorContent]' \
      --output text
    exit 1
  fi
done

echo ""
echo "Timed out (2.5 min) — check manually:"
echo "  aws ssm get-command-invocation --command-id ${CMD_ID} --instance-id ${INSTANCE_ID}"
exit 1
