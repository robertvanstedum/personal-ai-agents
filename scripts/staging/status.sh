#!/bin/bash
# status.sh — release state, containers, image tags against RELEASE, health,
# port holders. Unlike up/down/verify it still runs when the release worktree
# does not match RELEASE, and says so.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_absolute_root
require_release_files
TAG=$(release_tag)

echo "== release"
sed -n -E 's/^(sha|tag|ref|built_at)=/  \1=/p' "$STAGING_RELEASE_FILE"
mismatch=$(release_mismatch)
if [[ -n "$mismatch" ]]; then
  echo "  MISMATCH: $mismatch"
else
  echo "  release worktree: at the pinned sha"
fi
if [[ -f "$STAGING_BUILD_FAILED" ]]; then
  echo "  LAST BUILD FAILED:"
  sed 's/^/    /' "$STAGING_BUILD_FAILED"
fi
echo "  bots profile: $(bots_enabled && echo on || echo off)"

echo "== containers (project $STAGING_PROJECT)"
printf '  %-24s %-10s %-10s %-9s %s\n' NAME STATE HEALTH RESTARTS IMAGE
for name in "${STAGING_CORE_CONTAINERS[@]}" "${STAGING_BOT_CONTAINERS[@]}"; do
  if ! docker inspect "$name" >/dev/null 2>&1; then
    printf '  %-24s %s\n' "$name" "absent"
    continue
  fi
  fmt='{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}-{{end}} {{.RestartCount}} {{.Config.Image}} {{index .Config.Labels "com.docker.compose.project"}}'
  read -r state health restarts image project < <(docker inspect --format "$fmt" "$name")
  flag=""
  [[ "$project" == "$STAGING_PROJECT" ]] || flag=" (project ${project:-none}, NOT staging)"
  if [[ "$image" == minimoi-staging/* && "$image" != *":$TAG" && "$image" != *"-$TAG" ]]; then
    flag="$flag (image is not release $TAG)"
  fi
  printf '  %-24s %-10s %-10s %-9s %s%s\n' "$name" "$state" "$health" "$restarts" "$image" "$flag"
done

echo "== host port holders (127.0.0.1)"
for port in $STAGING_HOST_PORTS; do
  holders=$(port_listeners "$port" | tr '\n' ' ')
  foreign=$(foreign_port_holder "$port")
  printf '  %-6s %s%s\n' "$port" "${holders:-none}" "${foreign:+  (NOT staging: $foreign)}"
done
