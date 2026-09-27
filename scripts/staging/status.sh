#!/bin/bash
# status.sh — containers, image tags against RELEASE, health, port holders.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_absolute_root
require_release
TAG=$(release_tag)

echo "== release"
sed -n -E 's/^(sha|tag|ref|built_at)=/  \1=/p' "$STAGING_RELEASE_FILE"
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
for port in 5001 5432 8766 8767 8769 8770 14000 18790; do
  holders=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk 'NR>1 {print $1"("$2")"}' | sort -u | tr '\n' ' ' || true)
  printf '  %-6s %s\n' "$port" "${holders:-none}"
done
