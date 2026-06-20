#!/bin/bash
# Sync data files from Mac to EC2.
# Usage: ./scripts/sync_data.sh <ec2-public-ip>
# Requires: SSH key configured for ec2-user@<ip>
set -euo pipefail

EC2_IP="${1:?Usage: sync_data.sh <ec2-public-ip>}"
EC2_USER="ec2-user"
EC2_DATA="/opt/minimoi/data"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Syncing curator data files ==="
rsync -avz --progress \
  "${REPO_ROOT}/curator_signals.json" \
  "${REPO_ROOT}/curator_latest.json" \
  "${REPO_ROOT}/curator_radar.json" \
  "${EC2_USER}@${EC2_IP}:${EC2_DATA}/"

echo "=== Syncing German domain data ==="
rsync -avz --progress \
  "${REPO_ROOT}/domains/german/data/" \
  "${EC2_USER}@${EC2_IP}:${EC2_DATA}/german/"

echo "=== Sync complete ==="
echo "Data is at ${EC2_DATA} on ${EC2_IP}"
