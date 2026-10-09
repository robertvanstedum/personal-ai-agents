#!/bin/bash
# codex-room.sh — Codex's own ChatGPT sign-in for Rooms (ROOMS_R2.md v0.4 §3.6.5).
#
# Usage (owner-run):
#   codex-room.sh login    device-code sign-in inside the rooms-codex container:
#                          open the URL it shows, enter the code, use your ChatGPT
#                          account. The sign-in stays in Codex's own volume; nothing
#                          is copied from ~/.codex on this Mac.
#   codex-room.sh status   "Logged in using ChatGPT" or "Not logged in"
#   codex-room.sh logout   remove that sign-in
#
# No credential is ever printed by this script.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
CONTAINER="minimoi-rooms-codex"
AUTH=(-c 'forced_login_method="chatgpt"' -c 'cli_auth_credentials_store="file"')

[[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || true)" == true ]] \
  || die "$CONTAINER is not running (records.sh provision-codex, then records.sh up)"

case "${1:-}" in
  login)  docker exec -it "$CONTAINER" codex login --device-auth "${AUTH[@]}" ;;
  status) docker exec "$CONTAINER" codex login status "${AUTH[@]}" ;;
  logout) docker exec "$CONTAINER" codex logout ;;
  *) die "usage: codex-room.sh login|status|logout" ;;
esac
