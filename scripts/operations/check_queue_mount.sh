#!/bin/bash
# check_queue_mount.sh — post-deploy proof that the portal writes the live queue.
#
# Run on EC2 after every deploy (.github/workflows/deploy.yml). Fails the
# release unless:
#   * the portal container mounts the host queue FOLDER at /app/runtime/guild,
#   * its GUILD_QUEUE_PATH points into that mount,
#   * Save is ON: the store's own gate, QueueStore.write_problem(), run inside
#     the running container, returns None (it checks os.path.ismount on the
#     real bind mount, which no unit test can), and
#   * the queue's SHA-256 inside the container equals the host file's.
# A portal that lost its mount would otherwise Save into its own image layer
# while /health stays green.
#
# Usage: check_queue_mount.sh [HOST_QUEUE] [CONTAINER] [CONTAINER_QUEUE]

set -euo pipefail

HOST_QUEUE="${1:-/opt/minimoi/data/guild/build_queue.json}"
CONTAINER="${2:-minimoi-portal}"
CONTAINER_QUEUE="${3:-/app/runtime/guild/build_queue.json}"
HOST_DIR="$(dirname "$HOST_QUEUE")"
MOUNT_DIR="$(dirname "$CONTAINER_QUEUE")"

MOUNTS=$(docker inspect --format '{{range .Mounts}}{{.Source}}={{.Destination}};{{end}}' "$CONTAINER")
case ";$MOUNTS" in
  *";$HOST_DIR=$MOUNT_DIR;"*) ;;
  *) echo "queue check FAILED: $CONTAINER does not mount $HOST_DIR at $MOUNT_DIR (mounts: $MOUNTS)" >&2; exit 1 ;;
esac

ENV_PATH=$(docker exec "$CONTAINER" printenv GUILD_QUEUE_PATH || true)
if [ "$ENV_PATH" != "$CONTAINER_QUEUE" ]; then
  echo "queue check FAILED: GUILD_QUEUE_PATH in $CONTAINER is '$ENV_PATH', expected $CONTAINER_QUEUE" >&2
  exit 1
fi

# Ask the store itself whether Save is on in the running portal. The Python is
# in single quotes and uses only double quotes inside, and this script reaches
# EC2 base64-encoded, so no SSM or JSON quoting touches it.
SAVE_PROBE='import os, sys
from domains.guild.queue_store import QueueStore
problem = QueueStore(os.environ.get("GUILD_QUEUE_PATH")).write_problem()
print(problem or "writable")
sys.exit(1 if problem else 0)'
if ! SAVE_STATE=$(docker exec "$CONTAINER" python3 -c "$SAVE_PROBE"); then
  echo "queue check FAILED: Save is off in $CONTAINER: $SAVE_STATE" >&2
  exit 1
fi
if [ "$SAVE_STATE" != "writable" ]; then
  echo "queue check FAILED: unexpected Save state from $CONTAINER: $SAVE_STATE" >&2
  exit 1
fi
echo "queue check: Save is on in $CONTAINER"

# A Save can land between the two reads; retry a few times before failing.
for attempt in 1 2 3; do
  HOST_SUM=$(sha256sum "$HOST_QUEUE" | cut -d' ' -f1)
  CONTAINER_SUM=$(docker exec "$CONTAINER" sha256sum "$CONTAINER_QUEUE" | cut -d' ' -f1)
  if [ -n "$HOST_SUM" ] && [ "$HOST_SUM" = "$CONTAINER_SUM" ]; then
    echo "queue check OK: $CONTAINER sees the live queue (sha256 $HOST_SUM)"
    exit 0
  fi
  echo "[$attempt/3] queue checksum differs: host $HOST_SUM, container $CONTAINER_SUM"
  sleep 2
done
echo "queue check FAILED: the portal's queue is not the host's live file" >&2
exit 1
