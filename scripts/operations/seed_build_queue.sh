#!/bin/bash
# seed_build_queue.sh — deploy-time guard for the live Guild Build Queue on EC2.
#
# The production portal writes /opt/minimoi/data/guild/build_queue.json when
# Robert saves a status (owner Save). A deploy must never replace that live
# file with the repository copy. This script therefore:
#   * seeds the queue from SOURCE only when the host has no queue yet, or an
#     empty file (first deploy);
#   * otherwise keeps the live file untouched and saves a timestamped backup.
#
# Usage: seed_build_queue.sh SOURCE_URL [DEST] [BACKUP_DIR]
#   SOURCE_URL  where to fetch the seed copy (any URL curl accepts, e.g. a
#               GitHub raw URL pinned to the deployed commit, or file:// in tests)
#   DEST        live queue path (default /opt/minimoi/data/guild/build_queue.json)
#   BACKUP_DIR  backup directory (default: <DEST dir>/backups)
#
# Deliberately publishing the repository copy over a live queue is a separate,
# explicit action: scripts/sync_docs.sh --publish-queue (it backs up first).

set -euo pipefail

SRC_URL="${1:?usage: seed_build_queue.sh SOURCE_URL [DEST] [BACKUP_DIR]}"
DEST="${2:-/opt/minimoi/data/guild/build_queue.json}"
BACKUP_DIR="${3:-$(dirname "$DEST")/backups}"

mkdir -p "$(dirname "$DEST")"

if [ -s "$DEST" ]; then
  mkdir -p "$BACKUP_DIR"
  STAMP=$(date -u +%Y%m%dT%H%M%SZ)
  cp -p "$DEST" "$BACKUP_DIR/build_queue.$STAMP.json"
  echo "build_queue: live copy kept, not overwritten by deploy (backup: $BACKUP_DIR/build_queue.$STAMP.json)"
  exit 0
fi

TMP="$DEST.seed.$$"
trap 'rm -f "$TMP"' EXIT
curl -fsSL "$SRC_URL" -o "$TMP"
if command -v python3 >/dev/null 2>&1; then
  python3 -m json.tool "$TMP" >/dev/null || { echo "build_queue: seed source is not valid JSON; nothing written" >&2; exit 1; }
fi
if [ -e "$DEST" ]; then
  # An existing (empty) file may be bind-mounted into a running container as a
  # single file: replacing it with mv would leave the container on the old
  # inode. Write the content into the same file instead. (Mounting the folder,
  # issue #233, is the proper fix; then an atomic replace is safe.)
  cat "$TMP" > "$DEST"
else
  mv "$TMP" "$DEST"
fi
rm -f "$TMP"
trap - EXIT
echo "build_queue: no live copy found; seeded from $SRC_URL"
