#!/bin/sh
# Apply the image's pinned openclaw.json to the Agent A state volume on EVERY
# start, then hand over to the gateway.
#
# Why: Docker seeds image files only into a newly created named volume. An
# existing state volume (staging, production) would otherwise keep whatever
# openclaw.json it was first created with, so a config change in the repo would
# never reach it. The managed openclaw.json and workspace AGENTS.md are
# replaced. Auth profiles, sessions, the state database and adaptable workspace
# files (SOUL, MEMORY and USER) are preserved.
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

# Existing volumes must receive the reviewed owner-directed runtime policy too.
# Preserve adaptable SOUL/MEMORY/USER files, credentials and sessions.
PINNED_POLICY=/opt/minimoi/cos-agent-a/AGENTS.md
policy_dest="${OPENCLAW_AGENT_POLICY_PATH:-$(dirname "$dest")/workspace-cos-agent-a/AGENTS.md}"
[ -f "$PINNED_POLICY" ] || { echo "cos-agent-a: managed policy missing; refusing to start" >&2; exit 1; }
mkdir -p "$(dirname "$policy_dest")"
if [ -f "$policy_dest" ] && ! cmp -s "$PINNED_POLICY" "$policy_dest"; then
  cp -p "$policy_dest" "$policy_dest.replaced-by-image"
fi
tmp="$policy_dest.image-apply.$$"
cp "$PINNED_POLICY" "$tmp"
chmod 600 "$tmp"
mv -f "$tmp" "$policy_dest"

exec "$@"
