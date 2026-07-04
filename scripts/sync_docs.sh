#!/bin/bash
# Sync build queue and spec docs from Mac to EC2.
# Run after updating build_queue.json or docs/design/ on dev.
# The portal reads these from host-mounted paths — no container restart needed.
#
# Usage: ./scripts/sync_docs.sh <ec2-public-ip>
# Requires: SSH key configured for ec2-user@<ip>
#
# One-time EC2 bootstrap (run on EC2 before first sync):
#   sudo mkdir -p /opt/minimoi/data/guild /opt/minimoi/docs/design /opt/minimoi/auth
#   sudo docker cp minimoi-portal:/app/minimoi_portal/auth/guests.json /opt/minimoi/auth/guests.json
#   sudo docker cp minimoi-portal:/app/data/guild/build_queue.json /opt/minimoi/data/guild/build_queue.json
#   # Then run this script to populate docs/design from Mac
set -euo pipefail

EC2_IP="${1:?Usage: sync_docs.sh <ec2-public-ip>}"
EC2_USER="ec2-user"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Syncing build queue ==="
rsync -avz \
  "${REPO_ROOT}/data/guild/build_queue.json" \
  "${EC2_USER}@${EC2_IP}:/opt/minimoi/data/guild/build_queue.json"

echo "=== Syncing spec docs ==="
rsync -avz --delete \
  "${REPO_ROOT}/docs/design/" \
  "${EC2_USER}@${EC2_IP}:/opt/minimoi/docs/design/"

echo "=== Done — prod reads from host paths, no restart needed ==="
