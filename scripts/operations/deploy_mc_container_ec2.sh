#!/bin/bash
# Master Craftsman's own container on EC2 (spec_mc_agent_container v0.2 §5 and §7b; runbook docs/runbooks/mc_container_prod.md).
#
#   deploy_mc_container_ec2.sh deploy <mc-agent-vX.Y.Z[-n]>   start or update the MC project. Never touches the portal.
#   deploy_mc_container_ec2.sh status                         containers, relay health and which chat partner the portal uses
#   deploy_mc_container_ec2.sh switch mc|cos                  point the Guild chat at MC's container (mc) or back at CoS (cos)
#   deploy_mc_container_ec2.sh collect                        move MC's capture blocks into the private judgment store
#   deploy_mc_container_ec2.sh approve-policy "<words>"       record Robert's approval of the exact prod policy digest
#
# The MC project is its own compose project (minimoi-mc). Tokens are generated on the host into /opt/minimoi/mc/mc.env
# (root, 0600) and never printed. The portal's caller token is the same relay caller token, written into
# /opt/minimoi/.env as MC_RUNTIME_TOKEN only by `switch mc`. Switching re-runs the ordinary scoped deploy for the portal
# alone at its current image tag, so it takes the exact path every release takes.
set -euo pipefail

ROOT=/opt/minimoi
MC_DIR=$ROOT/mc
MC_ENV=$MC_DIR/mc.env
MAIN_ENV=$ROOT/.env
COMPOSE_FILE=$ROOT/docker-compose.mc.prod.yml
JUDGMENT=$ROOT/private/judgment
JUDGMENT_TOOL=$ROOT/scripts/judgment_store.py
# MC's conversation records inside its own state volume (agent mc-chat). Read only through a copy: opening the live
# database from the host would create root-owned side files in MC's volume.
MC_DB_DIR=/var/lib/docker/volumes/minimoi-mc-state/_data/agents/mc-chat/agent
CRON_FILE=/etc/cron.d/minimoi-mc-judgment
REGISTRY=332704997792.dkr.ecr.us-east-1.amazonaws.com
RELAY_URL=http://mc-relay:8790/v1
CHAT_MODEL=openclaw/mc-chat
TAG_ERE='^mc-agent-v[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9]+(\.[A-Za-z0-9]+)*)?$'

log() { echo "mc-container: $*"; }
die() { echo "mc-container: REFUSED: $*" >&2; exit 2; }
mc_compose() { MC_IMAGE_TAG="$1" docker-compose -p minimoi-mc --env-file "$MC_ENV" -f "$COMPOSE_FILE" "${@:2}"; }
env_value() { sed -n "s/^$2=//p" "$1" | tail -1; }

relay_health() {
  # From inside the relay container (no host port exists). Prints the HTTP status only.
  docker exec minimoi-mc-relay node -e \
    "fetch('http://127.0.0.1:8790/healthz').then(r=>{console.log(r.status)}).catch(()=>{console.log('down')})" 2>/dev/null || echo down
}

prepare() {
  install -d -m 0750 -o 1000 -g 1000 "$MC_DIR/work" "$MC_DIR/inbox" "$MC_DIR/workshops" "$MC_DIR/apprenticeship"
  install -d -m 0700 -o root -g root "$MC_DIR"
  # The judgment store: owner-only and never mounted into any container (spec §7b). The collector runs as root.
  install -d -m 0700 -o root -g root "$ROOT/private" "$JUDGMENT"
  # Nightly collection inside the standing batch window (midnight-6am Chicago), after the other early jobs.
  ( umask 022
    printf 'CRON_TZ=America/Chicago\n50 4 * * * root %s collect >> %s/logs/mc-judgment-collect.log 2>&1\n' \
      "$ROOT/scripts/deploy_mc_container_ec2.sh" "$ROOT" > "$CRON_FILE" )
  if [[ ! -f "$MC_ENV" ]]; then
    ( umask 077
      printf 'MC_CONTAINER_GATEWAY_TOKEN=%s\nMC_RELAY_CALLER_TOKEN=%s\n' \
        "$(openssl rand -hex 32)" "$(openssl rand -hex 32)" > "$MC_ENV" )
    log "generated MC tokens in $MC_ENV (not printed)"
  fi
  chmod 0600 "$MC_ENV"
  docker network inspect minimoi-mc-portal >/dev/null 2>&1 \
    || die "network minimoi-mc-portal is missing; it is created by the main deploy (docker-compose.prod.yml)"
  # Phase 2: MC's own tools credential and the Mac agent worker's credential (the portal holds only their hashes),
  # and MC's capped search key minted inside the gateway (the master key never leaves it).
  grep -q '^MC_TOOLS_TOKEN=' "$MC_ENV" || printf 'MC_TOOLS_TOKEN=%s\n' "$(openssl rand -base64 48 | tr -d '/+=\n' | cut -c1-48)" >> "$MC_ENV"
  grep -q '^MC_WORKER_TOKEN=' "$MC_ENV" || printf 'MC_WORKER_TOKEN=%s\n' "$(openssl rand -base64 48 | tr -d '/+=\n' | cut -c1-48)" >> "$MC_ENV"
  grep -q '^MC_SEARCH_KEY=' "$MC_ENV" || mint_search_key
  # Governed artifacts (same path inside MC as the portal names) and a self-contained repo clone (main only).
  install -d -m 0750 -o 1000 -g 1000 "$MC_DIR/artifacts"
  if [[ ! -d "$MC_DIR/repo/.git" ]]; then
    git clone -q --depth 50 --single-branch --branch main https://github.com/robertvanstedum/personal-ai-agents.git "$MC_DIR/repo"
    chown -R 1000:1000 "$MC_DIR/repo"
  fi
  # The portal's MC files (mounted read-only at /run/secrets/mc) and stores. The policy comes from the release; its
  # authority record only from `approve-policy` with Robert's words; registries start empty.
  install -d -m 0700 -o root -g root "$MC_DIR/portal" "$ROOT/data/mc-turns" "$ROOT/data/mc-lessons"
  install -m 0600 -o root -g root "$ROOT/mc-release/mc-policy.prod.json" "$MC_DIR/portal/mc-policy.prod.json"
  [[ -f "$MC_DIR/portal/approved-handoff-registry.json" ]] || ( umask 077; echo '{"handoffs":{}}' > "$MC_DIR/portal/approved-handoff-registry.json" )
  [[ -f "$MC_DIR/portal/task-watch-registry.json" ]] || ( umask 077; echo '{"subscriptions":[]}' > "$MC_DIR/portal/task-watch-registry.json" )
  [[ -f "$MC_DIR/portal/enrollment.json" ]] || log "note: $MC_DIR/portal/enrollment.json absent until the Mac worker is enrolled (agent contact stays off)"
}

mint_search_key() {
  local out
  out=$(docker exec -i minimoi-model-gateway python3 - <<'PY'
import json, os, urllib.request as u
master = os.environ["LITELLM_MASTER_KEY"]
req = u.Request("http://127.0.0.1:4000/key/generate", method="POST",
    headers={"Authorization": "Bearer " + master, "Content-Type": "application/json"},
    data=json.dumps({"models": ["minimoi-cos-web-search"], "allowed_routes": ["/v1/responses", "/responses"],
                     "max_budget": 5.0, "budget_duration": "30d", "rpm_limit": 10,
                     "key_alias": "mc-search-" + os.urandom(3).hex(),
                     "metadata": {"role": "guild.mc.search", "caller": "deploy"}}).encode())
print(json.loads(u.urlopen(req, timeout=60).read())["key"])
PY
)
  [[ "$out" =~ ^sk-[A-Za-z0-9_-]{16,200}$ ]] || die "could not mint MC's search key"
  printf 'MC_SEARCH_KEY=%s\n' "$out" >> "$MC_ENV"
  log "minted MC's capped search key (\$5 / 30 days, search route only); not printed"
}

sha() { printf '%s' "$1" | sha256sum | cut -d' ' -f1; }

approve_policy() {
  # Robert's approval of the exact prod policy digest, in his own words. Nothing else writes the authority record.
  local words="${1:-}" digest revision
  [[ -n "$words" ]] || die 'approve-policy "<Robert'"'"'s exact words of approval>"'
  digest=$(docker exec minimoi-portal python3 -m core.mc_tools.policy digest /run/secrets/mc/mc-policy.prod.json)
  revision=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["revision"])' "$MC_DIR/portal/mc-policy.prod.json")
  ( umask 077; python3 - "$digest" "$revision" "$words" > "$MC_DIR/portal/mc-policy.prod.authority.json" <<'PY'
import datetime, json, sys
digest, revision, words = sys.argv[1:4]
print(json.dumps({"schema": "mc-policy-authority/1", "revision": revision, "digest": digest, "approved_by": "robert",
                  "approved_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "ref": words}))
PY
  )
  docker exec minimoi-portal python3 -m core.mc_tools.policy check /run/secrets/mc/mc-policy.prod.json \
    /run/secrets/mc/mc-policy.prod.authority.json || die "policy check failed"
  log "policy ${revision} (${digest:0:12}) approved by Robert"
}

deploy() {
  local tag="${1:-}"
  [[ "$tag" =~ $TAG_ERE ]] || die "invalid release tag '$tag'"
  test -f "$COMPOSE_FILE" || die "missing $COMPOSE_FILE"
  prepare
  aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null
  mc_compose "$tag" pull
  mc_compose "$tag" up -d --remove-orphans
  echo "$tag" > "$MC_DIR/RELEASE"
  for _ in $(seq 1 36); do
    [[ "$(relay_health)" == "200" ]] && break
    sleep 10
  done
  [[ "$(relay_health)" == "200" ]] || { docker logs --tail 40 minimoi-mc-agent 2>&1 | tail -20; die "relay not healthy"; }
  log "MC project on $tag; relay healthy. The portal is unchanged (switch separately)."
}

status() {
  docker ps -a --filter name=minimoi-mc- --format '{{.Names}} {{.Image}} {{.Status}}' | sort
  echo "release: $(cat "$MC_DIR/RELEASE" 2>/dev/null || echo none)"
  echo "relay health: $(relay_health)"
  echo "portal chat partner (.env): $(env_value "$MAIN_ENV" MINIMOI_GUILD_CHAT)"
  echo "portal chat partner (running): $(docker exec minimoi-portal printenv MINIMOI_GUILD_CHAT 2>/dev/null || echo unknown)"
}

set_env_keys() {
  # Rewrite only the named keys in /opt/minimoi/.env, atomically, keeping a timestamped backup. Values never printed.
  local backup="$MAIN_ENV.bak-mc-switch-$(date -u +%Y%m%dT%H%M%SZ)"
  cp -p "$MAIN_ENV" "$backup"
  local tmp; tmp=$(mktemp "$MAIN_ENV.mc-switch.XXXXXX")
  cp -p "$MAIN_ENV" "$tmp"
  while [[ $# -gt 0 ]]; do
    local key="$1" value="$2"; shift 2
    local next; next=$(mktemp "$MAIN_ENV.mc-next.XXXXXX")
    grep -v "^${key}=" "$tmp" > "$next" || true
    [[ -n "$value" ]] && printf '%s=%s\n' "$key" "$value" >> "$next"
    mv "$next" "$tmp"
  done
  chmod --reference="$backup" "$tmp"; chown --reference="$backup" "$tmp"
  mv "$tmp" "$MAIN_ENV"
  log "updated $MAIN_ENV (backup $backup)"
}

switch() {
  local to="${1:-}"
  local tag; tag=$(docker inspect --format '{{.Config.Image}}' minimoi-portal | sed 's/.*://')
  [[ -n "$tag" ]] || die "cannot read the running portal tag"
  case "$to" in
    mc)
      test -f "$MC_ENV" || die "MC project not deployed"
      [[ "$(relay_health)" == "200" ]] || die "MC relay is not healthy; not switching"
      [[ -f "$MC_DIR/portal/mc-policy.prod.authority.json" ]] || die "no policy authority yet (approve-policy)"
      local worker_sha=""
      [[ -f "$MC_DIR/portal/enrollment.json" ]] && worker_sha=$(sha "$(env_value "$MC_ENV" MC_WORKER_TOKEN)")
      set_env_keys MINIMOI_GUILD_CHAT mc MC_RUNTIME_URL "$RELAY_URL" MC_RUNTIME_CHAT_MODEL "$CHAT_MODEL" \
                   MC_RUNTIME_TOKEN "$(env_value "$MC_ENV" MC_RELAY_CALLER_TOKEN)" \
                   MINIMOI_MC_TOOLS 1 MINIMOI_MC_TOOLS_TOKEN_SHA256 "$(sha "$(env_value "$MC_ENV" MC_TOOLS_TOKEN)")" \
                   MINIMOI_MC_DELEGATION 1 MINIMOI_MC_AGENT_WORKER_TOKEN_SHA256 "$worker_sha" \
                   MINIMOI_MC_WORKER_PUBLIC "$([[ -n "$worker_sha" ]] && echo 1)"
      ;;
    cos)
      set_env_keys MINIMOI_GUILD_CHAT cos MC_RUNTIME_URL "" MC_RUNTIME_CHAT_MODEL "" MC_RUNTIME_TOKEN "" \
                   MINIMOI_MC_TOOLS "" MINIMOI_MC_TOOLS_TOKEN_SHA256 "" MINIMOI_MC_DELEGATION "" \
                   MINIMOI_MC_AGENT_WORKER_TOKEN_SHA256 "" MINIMOI_MC_WORKER_PUBLIC ""
      ;;
    *) die "switch mc|cos";;
  esac
  "$ROOT/scripts/deploy_scoped_release.sh" "$tag" portal
  log "portal chat partner now: $(docker exec minimoi-portal printenv MINIMOI_GUILD_CHAT)"
  docker logs --since 5m minimoi-portal 2>&1 | grep -E "guild mounts: production activation" | tail -1 || true
}

collect() {
  test -f "$MC_DB_DIR/openclaw-agent.sqlite" || { log "no MC records yet; nothing to collect"; return 0; }
  local copy; copy=$(mktemp -d /root/mc-judgment-copy.XXXXXX)
  trap 'rm -rf "$copy"' RETURN
  # The database and its write-ahead log together, so the copy holds every committed turn.
  cp -p "$MC_DB_DIR/openclaw-agent.sqlite" "$copy/"
  [[ -f "$MC_DB_DIR/openclaw-agent.sqlite-wal" ]] && cp -p "$MC_DB_DIR/openclaw-agent.sqlite-wal" "$copy/"
  python3 "$JUDGMENT_TOOL" --store "$JUDGMENT" --db "$copy/openclaw-agent.sqlite" collect --apply
}

case "${1:-}" in
  deploy) deploy "${2:-}";;
  status) status;;
  switch) switch "${2:-}";;
  collect) collect;;
  approve-policy) approve_policy "${2:-}";;
  *) echo "usage: $0 deploy <mc-agent-vX.Y.Z> | status | switch mc|cos | collect | approve-policy \"<words>\"" >&2; exit 2;;
esac
