#!/bin/sh
# Apply the image's pinned openclaw.json to the Agent A state volume on EVERY
# start, then hand over to the gateway.
#
# Why: Docker seeds image files only into a newly created named volume. An
# existing state volume (staging, production) would otherwise keep whatever
# openclaw.json it was first created with, so a config change in the repo would
# never reach it. Only openclaw.json is replaced. Everything else on the volume
# (auth profiles, sessions, the state database, the workspace) is left as is.
#
# If the volume held a different file, one copy of it is kept beside the new
# one as openclaw.json.replaced-by-image (the previous config, for rollback).
set -eu

PINNED_CONFIG=/opt/minimoi/cos-agent-a/openclaw.json
dest="${OPENCLAW_CONFIG_PATH:-/home/node/.openclaw/openclaw.json}"

[ -f "$PINNED_CONFIG" ] || {
  echo "cos-agent-a: pinned config $PINNED_CONFIG is missing; refusing to start" >&2
  exit 1
}

mkdir -p "$(dirname "$dest")"
if [ -f "$dest" ] && ! cmp -s "$PINNED_CONFIG" "$dest"; then
  cp -p "$dest" "$dest.replaced-by-image"
  echo "cos-agent-a: applied the image's pinned openclaw.json (previous file kept as $(basename "$dest").replaced-by-image)" >&2
fi

tmp="$dest.image-apply.$$"
cp "$PINNED_CONFIG" "$tmp"
chmod 600 "$tmp"
mv -f "$tmp" "$dest"

exec "$@"
