#!/bin/bash
# check_queue_mount.sh — post-deploy proof that the portal writes the live queue.
#
# Run on EC2 after every deploy (.github/workflows/deploy.yml). Fails the
# release unless:
#   * the portal container mounts the host queue FOLDER at /app/runtime/guild,
#   * its GUILD_QUEUE_PATH points into that mount, and
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
