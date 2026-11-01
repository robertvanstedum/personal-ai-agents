#!/bin/bash
# aws_mc_tools.sh — MC's governed tools on dev (the AWS staging host, /opt/minimoi-staging): the staging counterpart of
# production's `deploy_mc_container_ec2.sh approve-policy` and `switch mc`, for the tool switches only. MC chat, its
# relay and its container are untouched. Runs on the host as root (through SSM).
#
#   aws_mc_tools.sh status                  the switches, the listener, and whether MC's runtime is recorded (no values)
#   aws_mc_tools.sh approve-policy "<words>" record Robert's approval of the exact policy digest, in his words, for dev
#   aws_mc_tools.sh on                      switch the tools on: needs the authority and dev's names in the rendered
#                                           compose (docker-compose.staging-mc-tools.yml); recreates the portal only;
#                                           waits for the listener and for MC's runtime to be recorded
#   aws_mc_tools.sh off                     switch the tools off; recreates the portal only
#   aws_mc_tools.sh issues on|off           MC's GitHub issue writes (new issues, comments) in the real repository:
#                                           projects dev's own issues token from Parameter Store into the portal's MC
#                                           folder (root 0600, never printed); each write still follows the policy
#
# Why it exists: dev moved to AWS on 30 Oct with MC chat on and its tools off, and nothing showed it; every MiniMoi tool
# MC called was refused (planning-studio/operations/threads/DIAG_MC_TOOLS_RUNTIME_NOT_RECORDED_2026-11-01.md).
# Tokens are never printed; the portal holds only the hash of MC's tool credential.
set -euo pipefail

ROOT=${MINIMOI_STAGING_ROOT:-/opt/minimoi-staging}
COMPOSE=${MINIMOI_STAGING_COMPOSE:-$ROOT/bin/compose.sh}
PORTAL_ENV=$ROOT/mc-portal.env                      # compose.sh passes it as --env-file when present
MC_ENV=$ROOT/mc/mc.env                              # MC's own tokens (MC_TOOLS_TOKEN is the plugin's credential)
POLICY=$ROOT/mc/portal/mc-policy.prod.json          # the MC release's policy, mounted read-only at /run/secrets/mc
AUTHORITY=$ROOT/mc/portal/mc-policy.prod.authority.json
IN_PORTAL=/run/secrets/mc
DB=${MINIMOI_STAGING_DB:-personal_agents}
# Dev's own credential (keys per agent and environment), never production's. Robert, 1 Nov: "MC is boss of workshop. It
# has full github rights" -- MC on dev files in the real repository.
ISSUES_TOKEN_PARAM=${MINIMOI_STAGING_ISSUES_PARAM:-/minimoi/staging/mc_github_issues_token}
ISSUES_TOKEN_FILE=$ROOT/mc/portal/github-issues-token

log() { echo "mc-tools-dev: $*"; }
die() { echo "mc-tools-dev: REFUSED: $*" >&2; exit 2; }
env_value() { [[ -f "$1" ]] && sed -n "s/^$2=//p" "$1" | tail -1 || true; }
sha() { printf '%s' "$1" | sha256sum | cut -d' ' -f1; }
portal_env() { docker exec minimoi-portal printenv "$1" 2>/dev/null || true; }
psql_value() { docker exec postgres-ai-agents psql -U postgres -d "$DB" -Atc "$1" 2>/dev/null || true; }

listener_health() {
  # From MC's side, at the address MC's plugin uses (loopback 5011, forwarded to the portal by the tools door). The door's
  # own name does not answer from inside MC (1 Nov: it read "down" while the plugin's calls were recorded). Status only.
  docker exec minimoi-mc-agent node -e \
    "fetch('http://127.0.0.1:5011/healthz').then(r=>console.log(r.status)).catch(()=>console.log('down'))" \
    2>/dev/null || echo down
}

recorded_since_start() {
  # "yes" when the current runtime row was registered after MC's gateway process started (its announcement landed).
  local started registered
  started=$(docker inspect -f '{{.State.StartedAt}}' minimoi-mc-agent 2>/dev/null || true)
  registered=$(psql_value "select to_char(registered_at at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS') from guild.mc_tool_runtime where environment='dev'")
  [[ -n "$started" && -n "$registered" && "$registered" > "${started:0:19}" ]] && echo yes || echo "no (recorded ${registered:-never}, MC started ${started:0:19})"
}

rendered_names_dev() {
  # The portal's environment as compose would render it, checked without printing any value.
  "$COMPOSE" config --format json 2>/dev/null | python3 -c '
import json, sys
env = json.load(sys.stdin)["services"]["portal"].get("environment") or {}
names = ("MINIMOI_MC_TOOLS_ENV", "MINIMOI_BOARD_ENV", "MINIMOI_MC_LESSONS_ENV", "MINIMOI_MC_DELEGATION_ENV")
bad = [n for n in names if env.get(n) != "dev"]
print(",".join(bad) or "ok")' 2>/dev/null || echo "unrenderable"
}

set_env_keys() {
  # Rewrite only the named keys in mc-portal.env, atomically, with a timestamped backup. Values never printed.
  [[ -f "$PORTAL_ENV" ]] || ( umask 077; : > "$PORTAL_ENV" )
  local backup; backup="$PORTAL_ENV.bak-mc-tools-$(date -u +%Y%m%dT%H%M%SZ)"
  cp -p "$PORTAL_ENV" "$backup"
  local tmp; tmp=$(mktemp "$PORTAL_ENV.mc-tools.XXXXXX")
  cp -p "$PORTAL_ENV" "$tmp"
  while [[ $# -gt 0 ]]; do
    local key="$1" value="$2" next; shift 2
    next=$(mktemp "$PORTAL_ENV.mc-next.XXXXXX")
    grep -v "^${key}=" "$tmp" > "$next" || true
    [[ -n "$value" ]] && printf '%s=%s\n' "$key" "$value" >> "$next"
    mv "$next" "$tmp"
  done
  chmod 0600 "$tmp"; chown --reference="$backup" "$tmp" 2>/dev/null || true
  mv "$tmp" "$PORTAL_ENV"
  log "updated $PORTAL_ENV (backup $backup)"
}

status() {
  local want got
  want=$(sha "$(env_value "$MC_ENV" MC_TOOLS_TOKEN)")
  got=$(portal_env MINIMOI_MC_TOOLS_TOKEN_SHA256)
  echo "switch (running portal): MINIMOI_MC_TOOLS=$(portal_env MINIMOI_MC_TOOLS) MINIMOI_MC_TOOLS_ENV=$(portal_env MINIMOI_MC_TOOLS_ENV) MINIMOI_BOARD_ENV=$(portal_env MINIMOI_BOARD_ENV)"
  echo "credential hash matches MC's: $([[ -n "$got" && "$got" == "$want" ]] && echo yes || echo no)"
  echo "policy authority: $([[ -f "$AUTHORITY" ]] && echo present || echo missing)"
  echo "compose renders dev names: $(rendered_names_dev)"
  echo "listener (from MC): $(listener_health)"
  echo "MC runtime recorded since its start: $(recorded_since_start)"
  echo "issue writes: tickets=$(portal_env MINIMOI_MC_TOOLS_TICKETS) comments=$(portal_env MINIMOI_MC_TOOLS_COMMENTS)"
}

approve_policy() {
  # Robert's approval of the exact policy digest, in his own words. Nothing else writes the authority record.
  local words="${1:-}" digest revision
  [[ -n "$words" ]] || die 'approve-policy "<Robert'"'"'s exact words of approval>"'
  [[ -f "$POLICY" ]] || die "no policy at $POLICY (the MC release's policy)"
  digest=$(docker exec minimoi-portal python3 -m core.mc_tools.policy digest "$IN_PORTAL/mc-policy.prod.json")
  [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || die "the portal could not digest the policy"
  revision=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["revision"])' "$POLICY")
  ( umask 077; python3 - "$digest" "$revision" "$words" > "$AUTHORITY" <<'PY'
import datetime, json, sys
digest, revision, words = sys.argv[1:4]
print(json.dumps({"schema": "mc-policy-authority/1", "revision": revision, "digest": digest, "approved_by": "robert",
                  "approved_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "ref": words}))
PY
  )
  docker exec minimoi-portal python3 -m core.mc_tools.policy check "$IN_PORTAL/mc-policy.prod.json" \
    "$IN_PORTAL/mc-policy.prod.authority.json" >/dev/null || { rm -f "$AUTHORITY"; die "policy check failed"; }
  log "policy ${revision} (${digest:0:12}) approved by Robert for dev"
}

recreate_portal() {
  "$COMPOSE" up -d --no-deps portal
}

switch_on() {
  [[ -f "$AUTHORITY" ]] || die "no policy authority yet (approve-policy with Robert's words)"
  local token; token=$(env_value "$MC_ENV" MC_TOOLS_TOKEN)
  [[ ${#token} -ge 32 ]] || die "MC's tool credential is missing from $MC_ENV (MC's deploy writes it)"
  local names; names=$(rendered_names_dev)
  [[ "$names" == ok ]] || die "compose does not render dev's names ($names): include docker-compose.staging-mc-tools.yml in compose.sh"
  set_env_keys MINIMOI_MC_TOOLS 1 MINIMOI_MC_TOOLS_TOKEN_SHA256 "$(sha "$token")"
  recreate_portal
  local health=down recorded=no
  for _ in $(seq 1 18); do
    health=$(listener_health); [[ "$health" == 200 ]] && break; sleep 5
  done
  [[ "$health" == 200 ]] || { docker logs --since 3m minimoi-portal 2>&1 | grep -E "mc tools:" | tail -5 || true; die "the tool listener did not come up"; }
  # The plugin retries its announcement with a back-off of at most 60 s; no MC restart is needed.
  for _ in $(seq 1 24); do
    recorded=$(recorded_since_start); [[ "$recorded" == yes ]] && break; sleep 5
  done
  [[ "$recorded" == yes ]] || die "the listener is up but MC's runtime is not recorded: $recorded"
  log "MC's tools are on for dev; MC's runtime is recorded"
}

switch_off() {
  rm -f "$ISSUES_TOKEN_FILE"
  set_env_keys MINIMOI_MC_TOOLS "" MINIMOI_MC_TOOLS_TOKEN_SHA256 "" MINIMOI_MC_TOOLS_TICKETS "" MINIMOI_MC_TOOLS_COMMENTS ""
  recreate_portal
  log "MC's tools are off for dev"
}

issues() {
  case "${1:-}" in
    on)
      [[ "$(env_value "$PORTAL_ENV" MINIMOI_MC_TOOLS)" == 1 ]] || die "MC's tools are off (on first)"
      local tmp; tmp=$(mktemp "$ROOT/mc/portal/.issues-token.XXXXXX")
      aws ssm get-parameter --region us-east-1 --name "$ISSUES_TOKEN_PARAM" --with-decryption \
        --query Parameter.Value --output text 2>/dev/null | tr -d '"\r\n' > "$tmp" \
        || { rm -f "$tmp"; die "cannot read $ISSUES_TOKEN_PARAM from Parameter Store"; }
      # A fine-grained token (github_pat_...) scoped to this repository; a classic token cannot be scoped to one repository.
      grep -qE '^github_pat_[A-Za-z0-9_]{20,240}$' "$tmp" \
        || { rm -f "$tmp"; die "$ISSUES_TOKEN_PARAM is not a fine-grained GitHub token (github_pat_...)"; }
      chmod 0600 "$tmp"; mv -f "$tmp" "$ISSUES_TOKEN_FILE"
      log "projected dev's MC GitHub token into $ISSUES_TOKEN_FILE (not printed)"
      set_env_keys MINIMOI_MC_TOOLS_TICKETS 1 MINIMOI_MC_TOOLS_COMMENTS 1
      ;;
    off)
      rm -f "$ISSUES_TOKEN_FILE"
      set_env_keys MINIMOI_MC_TOOLS_TICKETS "" MINIMOI_MC_TOOLS_COMMENTS ""
      ;;
    *) die "issues on|off";;
  esac
  recreate_portal
  echo "issue writes: tickets=$(portal_env MINIMOI_MC_TOOLS_TICKETS) comments=$(portal_env MINIMOI_MC_TOOLS_COMMENTS)"
}

case "${1:-}" in
  status) status;;
  approve-policy) approve_policy "${2:-}";;
  on) switch_on;;
  off) switch_off;;
  issues) issues "${2:-}";;
  *) echo "usage: $0 status | approve-policy \"<words>\" | on | off | issues on|off" >&2; exit 2;;
esac
