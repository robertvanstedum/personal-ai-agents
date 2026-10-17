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
COMPOSE=(docker-compose --project-name minimoi -f /opt/minimoi/docker-compose.prod.yml)
REGISTRY="332704997792.dkr.ecr.us-east-1.amazonaws.com/minimoi"

# Host-owned nonsecret activation survives image/Compose updates. Never source/eval it.
# Fail before any ECR/Docker mutation when present configuration is invalid.
ACTIVATION_ENV="$(python3 /opt/minimoi/scripts/production_activation.py)"
if [[ -n "$ACTIVATION_ENV" ]]; then
  while IFS='=' read -r activation_key activation_value; do
    export "$activation_key=$activation_value"
  done <<< "$ACTIVATION_ENV"
fi

# Typed optional Rooms binding; missing/invalid active custody refuses BEFORE mutations.
# Only the compiled fixed portal layer may be added; never source/eval host input.
RECORDS_ENV="$(python3 /opt/minimoi/scripts/rooms/deployment.py --portal-env)"
if [[ -n "$RECORDS_ENV" ]]; then
  while IFS='=' read -r records_key records_value; do
    export "$records_key=$records_value"
  done <<< "$RECORDS_ENV"
  COMPOSE+=(-f /opt/minimoi/docker-compose.records-production-portal.yml)
fi

python3 /opt/minimoi/scripts/release_status.py begin "$IMAGE_TAG" "${SERVICES[@]}"

cd /opt/minimoi
aws ecr get-login-password --region us-east-1 |
  docker login --username AWS --password-stdin 332704997792.dkr.ecr.us-east-1.amazonaws.com

export MINIMOI_IMAGE_TAG="$IMAGE_TAG"

# Snapshot COS Agent A's two volumes before its container is recreated.
# A newer OpenClaw migrates the config and state database in place on first
# start (one-way), so the only rollback is: old image + these files. The
# container is stopped first so the SQLite state is copied at rest.
#
# Once Agent A is stopped, an EXIT trap starts the old container again if
# anything fails before `up -d` has run (snapshot, checksum, disk full, ...),
# so a failed deploy never leaves Agent A down. Size report and pruning are
# best effort and never fail a deploy.
#
# Retention: sets are root-only. A set whose OpenClaw major.minor differs from
# the image being deployed is marked KEEP and never pruned (it is the only
# state the older OpenClaw can read). Of the sets taken from the same
# major.minor, the newest AGENT_A_SNAPSHOT_KEEP are kept.
# Restore steps: scripts/staging/README.md, "Agent A OpenClaw upgrade".
AGENT_A_CONTAINER="minimoi-cos-agent-a"
AGENT_A_SNAPSHOT_DIR="/opt/minimoi/backups/cos-agent-a"
AGENT_A_SNAPSHOT_KEEP=5
AGENT_A_VERSION_LABEL='{{index .Config.Labels "org.opencontainers.image.version"}}'
AGENT_A_STOPPED=0
AGENT_A_PENDING_SET=""

# Curator keeps some state only in its container (#305). Before it is
# recreated, curator_preserve.sh stops it and copies that state out,
# curator_seed_state.sh fills the durable host paths from that copy (never from
# dev or the image), and curator_bind_preflight.sh refuses a missing or
# wrong-type bind source. Any failure before `up -d` starts the old container
# again via the EXIT trap. Snapshots are never pruned here.
CURATOR_CONTAINER="minimoi-curator"
CURATOR_STOPPED=0
OPS_SCRIPTS="/opt/minimoi/scripts"

restart_stopped_on_exit() {
  local status=$?
  if [[ "$CURATOR_STOPPED" == "1" ]]; then
    echo "Deploy stopped while $CURATOR_CONTAINER was down; starting the previous container again."
    docker start "$CURATOR_CONTAINER" >/dev/null 2>&1 ||
      echo "WARNING: could not start $CURATOR_CONTAINER; start it by hand"
    [[ "$status" -ne 0 ]] || status=1
  fi
  if [[ "$AGENT_A_STOPPED" == "1" ]]; then
    echo "Deploy stopped while $AGENT_A_CONTAINER was down; starting the previous container again."
    if [[ -n "$AGENT_A_PENDING_SET" && ! -f "$AGENT_A_PENDING_SET/SNAPSHOT" ]]; then
      rm -rf -- "$AGENT_A_PENDING_SET" || true
    fi
    docker start "$AGENT_A_CONTAINER" >/dev/null 2>&1 ||
      echo "WARNING: could not start $AGENT_A_CONTAINER; start it by hand"
    [[ "$status" -ne 0 ]] || status=1
  fi
  exit "$status"
}
trap restart_stopped_on_exit EXIT

# "2026.7.1" -> "2026.7"; empty -> "unknown".
openclaw_line() {
  local version="${1:-}"
  [[ -n "$version" && "$version" != "<no value>" ]] || { echo unknown; return 0; }
  echo "${version%.*}"
}

prune_agent_a_snapshots() {
  local new_line="$1" set line
  local -a prunable=()
  while read -r set; do
    set="${set%/}"
    [[ -n "$set" && ! -e "$set/KEEP" ]] || continue
    line="$(sed -n 's/^openclaw_line=//p' "$set/SNAPSHOT" 2>/dev/null || true)"
    [[ "$line" == "$new_line" ]] || continue
    prunable+=("$set")
  done < <(ls -1d "$AGENT_A_SNAPSHOT_DIR"/*/ 2>/dev/null | sort)
  local excess=$(( ${#prunable[@]} - AGENT_A_SNAPSHOT_KEEP )) i
  for (( i = 0; i < excess; i++ )); do
    rm -rf -- "${prunable[$i]}"
  done
}

snapshot_agent_a_volumes() {
  if ! docker inspect "$AGENT_A_CONTAINER" >/dev/null 2>&1; then
    echo "No existing $AGENT_A_CONTAINER; no Agent A volumes to snapshot."
    return 0
  fi
  local stamp previous_image previous_line new_line dest name path archive size
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  previous_image="$(docker inspect --format='{{.Config.Image}}' "$AGENT_A_CONTAINER")"
  previous_line="$(openclaw_line "$(docker inspect --format="$AGENT_A_VERSION_LABEL" "$AGENT_A_CONTAINER" 2>/dev/null || true)")"
  new_line="$(openclaw_line "$(docker image inspect --format="$AGENT_A_VERSION_LABEL" "$REGISTRY/cos-scheduler:agent-a-$IMAGE_TAG" 2>/dev/null || true)")"
  dest="$AGENT_A_SNAPSHOT_DIR/$stamp"
  ( umask 077 && mkdir -p "$dest" )
  chmod 700 "$AGENT_A_SNAPSHOT_DIR"

  echo "Stopping $AGENT_A_CONTAINER to snapshot its volumes into $dest"
  AGENT_A_PENDING_SET="$dest"
  AGENT_A_STOPPED=1
  docker stop --time 30 "$AGENT_A_CONTAINER" >/dev/null
  for name in state auth; do
    case "$name" in
      state) path=/home/node/.openclaw ;;
      auth) path=/home/node/.config/openclaw ;;
    esac
    archive="$dest/cos-agent-a-$name.tar.gz"
    # docker cp reads the stopped container's mounted volume; no extra image.
    ( umask 077 && docker cp "$AGENT_A_CONTAINER:$path" - | gzip -c > "$archive.partial" )
    mv "$archive.partial" "$archive"
  done
  if [[ "$previous_line" != "$new_line" ]]; then
    ( umask 077 && echo "OpenClaw $previous_line -> $new_line; never pruned automatically" > "$dest/KEEP" )
  fi
  (
    umask 077
    {
      echo "taken_at=$stamp"
      echo "previous_image=$previous_image"
      echo "openclaw_line=$previous_line"
      echo "new_image_tag=$IMAGE_TAG"
      echo "new_openclaw_line=$new_line"
      cd "$dest" && sha256sum cos-agent-a-state.tar.gz cos-agent-a-auth.tar.gz
    } > "$dest/SNAPSHOT.partial"
  )
  mv "$dest/SNAPSHOT.partial" "$dest/SNAPSHOT"
  size="$(du -sh "$dest" 2>/dev/null | cut -f1)" || size="size unknown"
  echo "Agent A volumes snapshotted: $dest ($size); previous image $previous_image (OpenClaw $previous_line)"

  prune_agent_a_snapshots "$new_line" || echo "WARNING: Agent A snapshot pruning failed; continuing"
}

# Absence is proven by a successful listing; a Docker error fails the deploy
# instead of skipping preservation (#305 review).
curator_container_exists() {
  local names
  names="$(docker ps -a --format '{{.Names}}')" || { echo "Could not list Docker containers; not replacing Curator."; exit 1; }
  grep -qxF -- "$CURATOR_CONTAINER" <<< "$names"
}

preserve_curator_state() {
  if curator_container_exists; then
    local output snapshot
    # Set first: if the snapshot fails after stopping Curator, the trap restarts it.
    CURATOR_STOPPED=1
    output="$("$OPS_SCRIPTS/curator_preserve.sh" snapshot --keep-stopped)"
    echo "$output"
    snapshot="$(sed -n 's/^snapshot_dir=//p' <<< "$output")"
    [[ -n "$snapshot" ]] || { echo "Curator snapshot reported no folder; not replacing Curator."; exit 1; }
    "$OPS_SCRIPTS/curator_seed_state.sh" "$snapshot"
  else
    echo "No existing $CURATOR_CONTAINER; nothing to preserve or seed."
  fi
  "$OPS_SCRIPTS/curator_bind_preflight.sh"
}

# Preserve unaffected containers: pull and recreate only the selected services.
"${COMPOSE[@]}" pull "${SERVICES[@]}"
if [[ " ${SERVICES[*]} " == *" cos-agent-a "* ]]; then
  snapshot_agent_a_volumes
fi
if [[ " ${SERVICES[*]} " == *" curator "* ]]; then
  preserve_curator_state
fi
"${COMPOSE[@]}" up -d --no-deps --remove-orphans "${SERVICES[@]}"
# Agent A and Curator have been recreated (or were never stopped); nothing to restart now.
AGENT_A_STOPPED=0
CURATOR_STOPPED=0

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

if [[ " ${SERVICES[*]} " == *" curator "* ]]; then
  "$OPS_SCRIPTS/check_curator_mounts.sh"
fi

/opt/minimoi/scripts/install_lesen_refresh_cron.sh
runuser -u ec2-user -- /opt/minimoi/scripts/setup_ec2_cron.sh

# Prune only after the selected immutable release is healthy.
docker image prune -af

python3 /opt/minimoi/scripts/release_status.py finish "$IMAGE_TAG" "${SERVICES[@]}"
