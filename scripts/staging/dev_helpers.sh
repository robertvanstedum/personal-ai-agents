#!/bin/bash
# dev_helpers.sh up|status : the two helper containers on Dev. Run on the Dev host, as root, after an update that changes
# services/helper_seats: an operator update does not recreate them. Each seat sees only its own token and key.
# Expects /opt/minimoi-staging/helpers/secrets/{<seat>.token,<seat>.key,charter-approvals.json}. Secrets are never printed.
set -euo pipefail
HELP=/opt/minimoi-staging/helpers
image() { docker inspect minimoi-partnership-tasks --format '{{.Config.Image}}'; }
net_gw() { docker inspect minimoi-model-gateway --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' | awk '{print $1}'; }
case "${1:-status}" in
up)
  IMG=$(image); GW=$(net_gw)
  for seat in apprentice ea; do
    D="$HELP/secrets/seat-$seat"
    mkdir -p "$D" "$HELP/state/$seat"; chmod 700 "$D"
    cp -p "$HELP/secrets/$seat.token" "$HELP/secrets/$seat.key" "$HELP/secrets/charter-approvals.json" "$D/"
    chmod 400 "$D/$seat.token" "$D/$seat.key" "$D/charter-approvals.json"
    docker rm -f "minimoi-helper-$seat" >/dev/null 2>&1 || true
    docker create --name "minimoi-helper-$seat" --restart unless-stopped --network minimoi-staging-partnership \
      -e PARTNERSHIP_TASKS_URL=http://partnership-tasks:8772 -e PARTNERSHIP_ENVIRONMENT=dev -e HELPER_TOKEN_FILE="/run/helper/$seat.token" \
      -e HELPER_CHARTER_APPROVALS=/run/helper/charter-approvals.json -e HELPER_STATE_DIR=/state -e HELPER_REPO_ROOT=/repo -e GUILD_QUEUE_PATH=/guild/build_queue.json \
      -e HELPER_GATEWAY_URL=http://minimoi-model-gateway:4000/v1 -e HELPER_GATEWAY_KEY_FILE="/run/helper/$seat.key" -e HELPER_MODEL=minimoi-openai-low \
      -v "$D:/run/helper:ro" -v "$HELP/state:/state" -v /opt/minimoi-staging/mc/repo:/repo:ro -v /opt/minimoi-staging/data/guild:/guild:ro \
      --entrypoint python3 "$IMG" -m services.helper_seats.worker --seat "$seat" >/dev/null
    docker network connect "$GW" "minimoi-helper-$seat"
    docker start "minimoi-helper-$seat" >/dev/null
  done
  sleep 10
  docker ps -a --format '{{.Names}} {{.Status}}' | grep helper
  for seat in apprentice ea; do echo "$seat sees: $(docker exec "minimoi-helper-$seat" ls /run/helper | tr '\n' ' ')"; done;;
status) docker ps -a --format '{{.Names}} {{.Status}}' | grep helper || echo none;;
*) echo "usage: dev_helpers.sh up|status" >&2; exit 2;;
esac
