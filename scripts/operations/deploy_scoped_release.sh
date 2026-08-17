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

# Preserve unaffected containers: pull and recreate only the selected services.
"${COMPOSE[@]}" pull "${SERVICES[@]}"
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

for service in model-gateway cos-agent-a; do
  if [[ " ${SERVICES[*]} " == *" $service "* ]]; then
    container="minimoi-$service"
    for _ in $(seq 1 18); do
      [[ "$(docker inspect --format='{{.State.Health.Status}}' "$container")" == "healthy" ]] && break
      sleep 5
    done
    [[ "$(docker inspect --format='{{.State.Health.Status}}' "$container")" == "healthy" ]]
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
