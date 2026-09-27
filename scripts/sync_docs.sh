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
# Usage: ./scripts/sync_docs.sh [--publish-queue]
#   Default: docs/design and docs/specs only. The live Build Queue on EC2
#   (/opt/minimoi/data/guild/build_queue.json) is written by the production
#   portal when Robert saves a status, so it is NOT overwritten by default.
#   --publish-queue: deliberately replace the live queue with the committed
#   repository copy. It is a two-step, checked action:
#     1. `sync_docs.sh --publish-queue` alone publishes NOTHING. It prints the
#        live queue's SHA-256 and the verified Saves journalled since the last
#        seed/publish (seed_build_queue.sh --status on EC2).
#     2. After confirming those Saves are in the committed copy, run
#        `sync_docs.sh --publish-queue --expect-live-sha256=<that digest>`.
#        On EC2, seed_build_queue.sh --publish then takes the portal's queue
#        lock, refuses if the live file changed since step 1, refuses unless
#        the running portal mounts the queue folder, backs up the live file,
#        replaces it atomically (mode and owner kept), reads it back and
#        journals a `replaced` line.
#   A shell on the Mac cannot hold the EC2 lock across the two steps, so the
#   digest is the compare-and-swap: any Save between step 1 and step 2 changes
#   it and the publish is refused. Requires seed_build_queue.sh from the B1(a)
#   release on EC2. CI never passes this flag.
# Requires: aws CLI configured with minimoi-deploy credentials
#           (same credential used for ECR push and SSM parameter store)
# Test hook: SYNC_DOCS_DRY_RUN=1 prints the EC2 command lines and exits
#            before any AWS call.

set -euo pipefail

PUBLISH_QUEUE=0
EXPECT_LIVE=""
for arg in "$@"; do
  case "$arg" in
    --publish-queue) PUBLISH_QUEUE=1 ;;
    --expect-live-sha256=*) EXPECT_LIVE="${arg#--expect-live-sha256=}" ;;
    *) echo "unknown argument: $arg (usage: sync_docs.sh [--publish-queue [--expect-live-sha256=HEX]])" >&2; exit 2 ;;
  esac
done
if [[ -n "$EXPECT_LIVE" ]]; then
  [[ "$PUBLISH_QUEUE" == "1" ]] || { echo "--expect-live-sha256 only applies with --publish-queue" >&2; exit 2; }
  [[ "$EXPECT_LIVE" =~ ^[0-9a-f]{64}$ ]] || { echo "--expect-live-sha256 needs a 64-character lowercase hex digest" >&2; exit 2; }
fi

INSTANCE_ID="i-0d13db821169627e2"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GITHUB_REPO="robertvanstedum/personal-ai-agents"

# Guard: EC2 pulls from GitHub, not local disk — files must be committed.
GUARDED=(docs/design/ docs/specs/)
[[ "$PUBLISH_QUEUE" == "1" ]] && GUARDED+=(data/guild/build_queue.json)
DIRTY=$(git -C "$REPO_ROOT" diff --name-only HEAD -- "${GUARDED[@]}" 2>/dev/null || true)
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
LINES=(
  "set -e"
  "mkdir -p /opt/minimoi/data/guild /opt/minimoi/docs/design /opt/minimoi/docs/specs"
)
QUEUE_DEST="/opt/minimoi/data/guild/build_queue.json"
QUEUE_WRITER="/opt/minimoi/scripts/seed_build_queue.sh"
if [[ "$PUBLISH_QUEUE" == "1" && -z "$EXPECT_LIVE" ]]; then
  echo "Build Queue: NOT published. Step 1 of 2 prints the live queue's digest and recent Saves."
  echo "Build Queue: check those Saves are in the committed copy, then rerun with --expect-live-sha256=<digest>."
  LINES+=(
    "${QUEUE_WRITER} --status ${QUEUE_DEST}"
  )
elif [[ "$PUBLISH_QUEUE" == "1" ]]; then
  # The locked, checked, atomic, journalled publish (same lock as the portal).
  LINES+=(
    "${QUEUE_WRITER} --publish --expect-live-sha256 ${EXPECT_LIVE} '${RAW_BASE}/data/guild/build_queue.json' ${QUEUE_DEST} /opt/minimoi/data/guild/backups"
  )
else
  echo "Build Queue: live copy on EC2 left untouched (use --publish-queue to publish the repository copy deliberately)"
fi
for i in "${!SRCS[@]}"; do
  LINES+=("curl -fsSL '${SRCS[$i]}' -o '${DSTS[$i]}' && echo \"OK ($(( i + 1 ))/${TOTAL}): $(basename "${DSTS[$i]}")\"")
done
LINES+=("echo '=== done: ${TOTAL} files ==='")

if [[ "${SYNC_DOCS_DRY_RUN:-0}" == "1" ]]; then
  printf '%s\n' "${LINES[@]}"
  exit 0
fi

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
