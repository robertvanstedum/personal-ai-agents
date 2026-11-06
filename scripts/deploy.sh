#!/bin/bash
# Pull a selected ECR image set and restart containers.
# Run on EC2: bash /opt/minimoi/deploy.sh [seven-character commit tag]
# Omitting the tag retains the manual latest-tag fallback.
set -euo pipefail

ACCOUNT_ID="332704997792"
REGION="us-east-1"
ECR="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
COMPOSE=(docker-compose --project-name minimoi -f /opt/minimoi/docker-compose.prod.yml)
export MINIMOI_IMAGE_TAG="${1:-${MINIMOI_IMAGE_TAG:-latest}}"

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

# Host runtime P1-a's production layer: present on the host only once switched on (an operator act on Robert's word),
# then layered on every release so none drops it; absent, nothing changes. Never sourced or evaluated.
RUNTIME_LAYER=/opt/minimoi/docker-compose.runtime.prod.yml
if [[ -f "$RUNTIME_LAYER" ]]; then
  COMPOSE+=(-f "$RUNTIME_LAYER")
fi

# MiniMoi 1.2's production layer: present on the host only once switched on (an operator act on Robert's word), then
# layered on every release and every path through this script, so none strips its settings; absent, nothing changes.
MINIMOI_1_2_LAYER=/opt/minimoi/docker-compose.minimoi-1-2-production.yml
if [[ -f "$MINIMOI_1_2_LAYER" ]]; then
  COMPOSE+=(-f "$MINIMOI_1_2_LAYER")
fi

# Guildhall's production layer (scheduler and portal side): same rule as the scoped release's. On Robert's word only.
GUILDHALL_LAYER=/opt/minimoi/docker-compose.guildhall-production-main.yml
if [[ -f "$GUILDHALL_LAYER" ]]; then
  COMPOSE+=(-f "$GUILDHALL_LAYER")
fi

# MINIMOI_PARTNERSHIP_CARD_REF for this release; an exported value beats .env. EXACT=1: this release recreates the portal, so
# the commit must be this tag's. EXACT=0: the portal isn't recreated, so any good commit lets the layer render; with none,
# nothing is exported and the render below stops the release. Reads no other setting from the portal.
minimoi_1_2_card_ref() {   # TAG EXACT
  local tag="$1" exact="$2" candidate
  if [[ -n "${MINIMOI_RELEASE_COMMIT:-}" && ( ! "$MINIMOI_RELEASE_COMMIT" =~ ^[0-9a-f]{40}$ || ( "$exact" == 1 && "$MINIMOI_RELEASE_COMMIT" != "$tag"* ) ) ]]; then
    echo "MINIMOI_RELEASE_COMMIT is not this release's commit ($tag). Nothing was changed." >&2
    exit 1
  fi
  for candidate in "${MINIMOI_RELEASE_COMMIT:-}" "$tag" \
      "$(docker exec minimoi-portal printenv PARTNERSHIP_CARD_REF 2>/dev/null || true)" \
      "$(sed -n 's/^MINIMOI_PARTNERSHIP_CARD_REF=//p' /opt/minimoi/.env 2>/dev/null | tail -n 1)"; do
    [[ "$candidate" =~ ^[0-9a-f]{40}$ ]] || continue
    if [[ "$exact" == 0 || "$candidate" == "$tag"* ]]; then
      export MINIMOI_PARTNERSHIP_CARD_REF="$candidate"
      return 0
    fi
  done
  if [[ "$exact" == 1 ]]; then
    echo "No commit for portal release $tag: set MINIMOI_RELEASE_COMMIT to its 40-hex commit. Nothing was changed." >&2
    exit 1
  fi
}

# MiniMoi 1.2's images and profile: its companions run this release's images, never a value left on the host (an exported
# value beats .env). The profile keeps them in the project, so --remove-orphans can't remove them. A setting or layer that
# doesn't render stops the release here, before anything changes.
if [[ -f "$MINIMOI_1_2_LAYER" ]]; then
  COMPOSE+=(--profile minimoi-1-2)
  export MINIMOI_PORTAL_IMAGE="${ECR}/minimoi/portal:$MINIMOI_IMAGE_TAG"
  export MINIMOI_COS_SCHEDULER_IMAGE="${ECR}/minimoi/cos-scheduler:$MINIMOI_IMAGE_TAG"
  # The portal's task cards name the release's 40-hex commit, and the tag is only its first seven characters. Give it as
  # MINIMOI_RELEASE_COMMIT. Re-running the running tag keeps what the running portal has. "latest" names no commit to check.
  CARD_REF_EXACT=1
  [[ "$MINIMOI_IMAGE_TAG" != latest ]] || CARD_REF_EXACT=0
  minimoi_1_2_card_ref "$MINIMOI_IMAGE_TAG" "$CARD_REF_EXACT"
  "${COMPOSE[@]}" config -q || { echo "MiniMoi 1.2's layer does not render: setup is unfinished. Nothing was changed." >&2; exit 1; }
fi

python3 /opt/minimoi/scripts/release_status.py begin "$MINIMOI_IMAGE_TAG" portal curator german portuguese system-bot cos-bot cos-scheduler cos-agent-a model-gateway

echo "=== Authenticating with ECR ==="
aws ecr get-login-password --region ${REGION} | \
  docker login --username AWS --password-stdin ${ECR}

echo "=== Pulling image set: ${MINIMOI_IMAGE_TAG} ==="
"${COMPOSE[@]}" pull

echo "=== Restarting containers ==="
"${COMPOSE[@]}" up -d --remove-orphans

echo "=== Pruning old images ==="
docker image prune -f

echo "=== Container status ==="
"${COMPOSE[@]}" ps

python3 /opt/minimoi/scripts/release_status.py finish "$MINIMOI_IMAGE_TAG" portal curator german portuguese system-bot cos-bot cos-scheduler cos-agent-a model-gateway
