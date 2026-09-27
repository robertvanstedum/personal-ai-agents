#!/bin/bash
# Deploy only the immutable services selected by classify_release.py.

set -euo pipefail

IMAGE_TAG="${1:?immutable image tag required}"
shift
if [[ "$#" -eq 0 ]]; then
  echo "No services supplied; refusing an ambiguous deployment."
  exit 2
fi

SERVICES=("$@")
COMPOSE=(docker-compose -f /opt/minimoi/docker-compose.prod.yml)
REGISTRY="332704997792.dkr.ecr.us-east-1.amazonaws.com/minimoi"

cd /opt/minimoi
aws ecr get-login-password --region us-east-1 |
  docker login --username AWS --password-stdin 332704997792.dkr.ecr.us-east-1.amazonaws.com

export MINIMOI_IMAGE_TAG="$IMAGE_TAG"

# Snapshot COS Agent A's two volumes before its container is recreated.
# A newer OpenClaw migrates the config and state database in place on first
# start (one-way), so the only rollback is: old image + these files. The
# container is stopped first so the SQLite state is copied at rest; if the
# snapshot fails, the old container is started again and nothing is
# recreated. Snapshots are root-only and the newest AGENT_A_SNAPSHOT_KEEP sets
# are kept. Restore steps: scripts/staging/README.md, "Agent A OpenClaw upgrade".
AGENT_A_CONTAINER="minimoi-cos-agent-a"
AGENT_A_SNAPSHOT_DIR="/opt/minimoi/backups/cos-agent-a"
AGENT_A_SNAPSHOT_KEEP=5

snapshot_agent_a_volumes() {
  if ! docker inspect "$AGENT_A_CONTAINER" >/dev/null 2>&1; then
    echo "No existing $AGENT_A_CONTAINER; no Agent A volumes to snapshot."
    return 0
  fi
  local stamp previous_image dest name path archive
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  previous_image="$(docker inspect --format='{{.Config.Image}}' "$AGENT_A_CONTAINER")"
  dest="$AGENT_A_SNAPSHOT_DIR/$stamp"
  ( umask 077 && mkdir -p "$dest" )
  chmod 700 "$AGENT_A_SNAPSHOT_DIR"

  echo "Stopping $AGENT_A_CONTAINER to snapshot its volumes into $dest"
  docker stop --time 30 "$AGENT_A_CONTAINER" >/dev/null
  for name in state auth; do
    case "$name" in
      state) path=/home/node/.openclaw ;;
      auth) path=/home/node/.config/openclaw ;;
    esac
    archive="$dest/cos-agent-a-$name.tar.gz"
    # docker cp reads the stopped container's mounted volume; no extra image.
    if ! ( umask 077 && docker cp "$AGENT_A_CONTAINER:$path" - | gzip -c > "$archive.partial" ); then
      echo "Agent A $name snapshot FAILED; restarting the previous container and aborting."
      rm -f "$archive.partial"
      docker start "$AGENT_A_CONTAINER" >/dev/null || true
      return 1
    fi
    mv "$archive.partial" "$archive"
  done
  {
    echo "taken_at=$stamp"
    echo "previous_image=$previous_image"
    echo "new_image_tag=$IMAGE_TAG"
    ( cd "$dest" && sha256sum cos-agent-a-state.tar.gz cos-agent-a-auth.tar.gz )
  } > "$dest/SNAPSHOT"
  chmod 600 "$dest/SNAPSHOT"
  echo "Agent A volumes snapshotted: $dest ($(du -sh "$dest" | cut -f1)); previous image $previous_image"

  # Keep only the newest snapshot sets (directory names sort by time).
  ls -1d "$AGENT_A_SNAPSHOT_DIR"/*/ 2>/dev/null | sort | head -n "-$AGENT_A_SNAPSHOT_KEEP" |
    while read -r old; do rm -rf -- "$old"; done
}

# Preserve unaffected containers: pull and recreate only the selected services.
"${COMPOSE[@]}" pull "${SERVICES[@]}"
if [[ " ${SERVICES[*]} " == *" cos-agent-a "* ]]; then
  snapshot_agent_a_volumes
fi
"${COMPOSE[@]}" up -d --no-deps --remove-orphans "${SERVICES[@]}"

expected_image() {
  case "$1" in
    german) echo "$REGISTRY/mein-deutsch:$IMAGE_TAG" ;;
    cos-agent-a) echo "$REGISTRY/cos-scheduler:agent-a-$IMAGE_TAG" ;;
    model-gateway) echo "$REGISTRY/cos-scheduler:model-gateway-$IMAGE_TAG" ;;
    *) echo "$REGISTRY/$1:$IMAGE_TAG" ;;
  esac
}

for service in "${SERVICES[@]}"; do
  container="minimoi-$service"
  actual=$(docker inspect --format='{{.Config.Image}}' "$container")
  expected=$(expected_image "$service")
  [[ "$actual" == "$expected" ]] || {
    echo "$container uses $actual; expected $expected"
    exit 1
  }
  [[ "$(docker inspect --format='{{.State.Running}}' "$container")" == "true" ]] || {
    echo "$container is not running"
    exit 1
  }
done

wait_for_container_health() {
  local container="$1"
  local max_attempts="$2"
  local status="unknown"

  for attempt in $(seq 1 "$max_attempts"); do
    status=$(docker inspect --format='{{.State.Health.Status}}' "$container" 2>/dev/null || echo "missing")
    [[ "$status" == "healthy" ]] && return 0
    echo "[$attempt/$max_attempts] $container health: $status"
    sleep 5
  done

  echo "$container did not become healthy; final status: $status"
  docker inspect --format='{{json .State.Health}}' "$container" 2>/dev/null || true
  docker logs --tail 80 "$container" 2>&1 || true
  return 1
}

for service in model-gateway cos-agent-a; do
  if [[ " ${SERVICES[*]} " == *" $service "* ]]; then
    container="minimoi-$service"
    # OpenClaw's inherited Docker health check can remain in `starting` after
    # its HTTP gateway is ready. Allow up to six minutes, while still failing
    # closed with inspect/log evidence if the container never becomes healthy.
    wait_for_container_health "$container" 72
  fi
done

declare -A HEALTH_URLS=(
  [portal]="http://localhost:5001/health"
  [curator]="http://localhost:8766/health"
  [german]="http://localhost:8767/health"
  [portuguese]="http://localhost:8770/health"
  [cos-scheduler]="http://localhost:8769/health"
)
for service in "${SERVICES[@]}"; do
  if [[ -n "${HEALTH_URLS[$service]:-}" ]]; then
    for _ in $(seq 1 18); do
      curl -sf "${HEALTH_URLS[$service]}" && break
      sleep 5
    done
    curl -sf "${HEALTH_URLS[$service]}" >/dev/null
  fi
done

/opt/minimoi/scripts/install_lesen_refresh_cron.sh
runuser -u ec2-user -- /opt/minimoi/scripts/setup_ec2_cron.sh

# Prune only after the selected immutable release is healthy.
docker image prune -af
