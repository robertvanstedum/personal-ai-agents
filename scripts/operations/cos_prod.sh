#!/bin/bash
# Chief of Staff on EC2: its folders, its own capped gateway key, and what it can read (CoS gets Master Craftsman's
# working skills, Robert, 26 Oct 2026; #215, #335; runbook docs/runbooks/cos_prod.md).
#
#   cos_prod.sh prepare           create CoS's folders (every main release runs this before the deploy; idempotent)
#   cos_prod.sh key <dollars>     mint CoS's own capped key inside the gateway (CoS's four routes, chat completions and
#                                 responses only, a 30-day budget, rpm 60) and recreate only cos-agent-a, which then uses
#                                 it instead of the master key. A re-run rotates (the old key is deleted).
#   cos_prod.sh key off           back to the master key: the key file moves to /root, cos-agent-a is recreated
#   cos_prod.sh vault on|off      Robert's Confer conversation asks the Vault too (on), or only Guild ones (off);
#                                 recreates only cos-scheduler
#   cos_prod.sh status            which key CoS uses (never the value), folders, health
#   cos_prod.sh audit             from inside cos-agent-a, as CoS's own user: list every credential-like file it can read
#                                 (names only, never contents); exit 1 if any
#
# The key is never printed and never written to /opt/minimoi/.env (every main service loads that file). It lives in
# /opt/minimoi/cos/agent-key/key (root:1000 0640), mounted read-only into cos-agent-a only.
set -euo pipefail

ROOT=/opt/minimoi
COS=$ROOT/cos
KEY_DIR=$COS/agent-key
KEY_FILE=$KEY_DIR/key
AGENT=minimoi-cos-agent-a
GATEWAY=minimoi-model-gateway
MODELS='["minimoi-cos-agent", "minimoi-cos-agent-xai-fast", "minimoi-cos-web-search", "minimoi-cos-agent-anthropic"]'
ROUTES='["/v1/chat/completions", "/chat/completions", "/v1/responses", "/responses"]'

log() { echo "cos-prod: $*"; }
die() { echo "cos-prod: REFUSED: $*" >&2; exit 2; }
health() { docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$1" 2>/dev/null || echo absent; }

prepare() {
  install -d -m 0755 -o root -g root "$COS"
  # Written by cos-scheduler (root), read by CoS's runtime (node, uid 1000).
  install -d -m 0755 -o root -g root "$COS/inbox" "$COS/conversations"
  # Written by CoS's runtime.
  install -d -m 0750 -o 1000 -g 1000 "$COS/outbox" "$COS/planning-studio"
  install -d -m 0750 -o root -g 1000 "$KEY_DIR"
  # CoS 1.2 (spec v0.3 sections 4.5, 4.19): the store and the canonical history, written only by cos-scheduler (root)
  # and read by CoS's runtime through group 1000. The setgid bit makes every file and folder the writer creates
  # inherit group 1000 (domains/cos/store/fsio.py keeps it on new folders); files are 0640, folders 0750.
  install -d -m 2750 -o root -g 1000 "$COS/store" "$COS/conversations/history"
  # Planning Studio working copy, seeded once from MC's read-only repo clone (refreshed nightly by MC's cron).
  if [[ -d "$ROOT/mc/repo/planning-studio" && -z "$(ls -A "$COS/planning-studio")" ]]; then
    cp -R "$ROOT/mc/repo/planning-studio/." "$COS/planning-studio/"
    chown -R 1000:1000 "$COS/planning-studio"
    log "seeded the Planning Studio working copy from the repo clone"
  fi
  log "folders ready under $COS"
}

set_env_line() {
  # set_env_line KEY VALUE|--remove: rewrite one non-secret line of /opt/minimoi/.env atomically, keeping a backup.
  local key=$1 value=$2 env=$ROOT/.env backup tmp
  backup="$ROOT/.env.bak-cos-$(date -u +%Y%m%dT%H%M%SZ)"
  cp -p "$env" "$backup"
  tmp=$(mktemp "$env.cos.XXXXXX")
  grep -v "^${key}=" "$env" > "$tmp" || true
  if [[ "$value" != --remove ]]; then printf '%s=%s\n' "$key" "$value" >> "$tmp"; fi
  chmod --reference="$backup" "$tmp"; chown --reference="$backup" "$tmp"
  mv "$tmp" "$env"
  log "updated $env: $key (backup $backup)"
}

recreate_agent() {
  local tag; tag=$(docker inspect --format '{{.Config.Image}}' "$AGENT" | sed 's/.*:agent-a-//')
  [[ -n "$tag" ]] || die "cannot read cos-agent-a's running tag"
  log "recreating ONLY cos-agent-a at its running tag (CoS turns fail for about a minute)"
  "$ROOT/scripts/deploy_scoped_release.sh" "$tag" cos-agent-a
  for _ in $(seq 1 72); do [[ "$(health "$AGENT")" == healthy ]] && break; sleep 5; done
  [[ "$(health "$AGENT")" == healthy ]] || die "cos-agent-a is not healthy (docker logs $AGENT)"
  docker logs --since 10m "$AGENT" 2>&1 | grep -E "cos-agent-a: gateway key" | tail -1 || true
}

key_on() {
  local cap="${1:-}"
  [[ "$cap" =~ ^[0-9]{1,3}(\.[0-9]{1,2})?$ ]] && awk -v c="$cap" 'BEGIN{exit !(c>0 && c<=500)}' \
    || die "usage: cos_prod.sh key <monthly cap in dollars, 1-500> (Robert names the cap)"
  [[ "$(health "$GATEWAY")" == healthy ]] || die "the model gateway is not healthy"
  prepare
  local old=""; [[ -f "$KEY_FILE" ]] && old=$(cat "$KEY_FILE")
  local out
  out=$(COS_CAP="$cap" COS_OLD_KEY="$old" docker exec -i -e COS_CAP -e COS_OLD_KEY "$GATEWAY" python3 - <<PY
import json, os, urllib.request as u
master = os.environ["LITELLM_MASTER_KEY"]
def call(path, body):
    req = u.Request("http://127.0.0.1:4000" + path, method="POST", data=json.dumps(body).encode(),
                    headers={"Authorization": "Bearer " + master, "Content-Type": "application/json"})
    return json.loads(u.urlopen(req, timeout=60).read())
key = call("/key/generate", {"models": $MODELS, "allowed_routes": $ROUTES, "max_budget": float(os.environ["COS_CAP"]),
                             "budget_duration": "30d", "rpm_limit": 60, "key_alias": "cos-agent-" + os.urandom(3).hex(),
                             "metadata": {"role": "guild.cos", "caller": "cos_prod.sh"}})["key"]
print(key)
old = os.environ.get("COS_OLD_KEY", "")
if old.startswith("sk-") and old != key:
    try:
        call("/key/delete", {"keys": [old]})
    except Exception:
        pass
PY
) || die "the gateway did not make the key (nothing written; is its key database on?)"
  [[ "$out" =~ ^sk-[A-Za-z0-9_-]{16,200}$ ]] || die "the gateway's answer was not a key (nothing written)"
  ( umask 027; printf '%s\n' "$out" > "$KEY_FILE.tmp" )
  chown root:1000 "$KEY_FILE.tmp"; chmod 0640 "$KEY_FILE.tmp"; mv -f "$KEY_FILE.tmp" "$KEY_FILE"
  out=""
  log "CoS's own key: \$$cap per 30 days, models $MODELS, rpm 60 (not printed)"
  set_env_line COS_AGENT_MASTER_KEY ""
  recreate_agent
}

key_off() {
  if [[ -f "$KEY_FILE" ]]; then
    install -d -m 0700 /root/cos-key-retired
    mv "$KEY_FILE" "/root/cos-key-retired/key-$(date -u +%Y%m%dT%H%M%SZ)"
  fi
  set_env_line COS_AGENT_MASTER_KEY --remove
  recreate_agent
  log "CoS uses the master key again (the retired key is in /root/cos-key-retired; it still exists in the gateway)"
}

vault() {
  # Robert's Confer conversation ("owner") asks the Vault too (Robert, 26 Oct 2026), or back to Guild only.
  case "${1:-}" in
    on) set_env_line MINIMOI_COS_VAULT_CONVERSATIONS "guild-,owner" ;;
    off) set_env_line MINIMOI_COS_VAULT_CONVERSATIONS --remove ;;
    *) die "usage: cos_prod.sh vault on|off" ;;
  esac
  local tag; tag=$(docker inspect --format '{{.Config.Image}}' minimoi-cos-scheduler | sed 's/.*://')
  "$ROOT/scripts/deploy_scoped_release.sh" "$tag" cos-scheduler
  log "Vault conversations now: $(docker exec minimoi-cos-scheduler printenv MINIMOI_VAULT_ASK_CONVERSATIONS)"
}

status() {
  if [[ -s "$KEY_FILE" ]]; then echo "CoS key: its own capped key ($KEY_FILE)"; else echo "CoS key: the gateway's master key"; fi
  echo "running: $(docker logs "$AGENT" 2>&1 | grep -E 'cos-agent-a: gateway key' | tail -1 || echo unknown)"
  ls -ld "$COS" "$COS"/* 2>/dev/null
  echo "cos-agent-a: $(health "$AGENT")"
  echo "Vault conversations: $(docker exec minimoi-cos-scheduler printenv MINIMOI_VAULT_ASK_CONVERSATIONS 2>/dev/null || echo unknown)"
}

audit() {
  # Names only. Runs as the container's own user, so it sees exactly what CoS's tools can open.
  local found
  found=$(docker exec "$AGENT" sh -c 'find /minimoi /run/cos-key /home/node -readable -type f \( \
      -name "*.env" -o -name ".env*" -o -name "*.token" -o -name "*token*.json" -o -name "*-key.txt" \
      -o -name "*.pem" -o -name "id_rsa*" -o -name "id_ed25519*" -o -name "users.json" -o -name "guests.json" \
      -o -name "credentials*" -o -name "*secret*" -o -name "mc.env" -o -name "gateway-keys.env" \) 2>/dev/null' \
    | grep -v -E '^/run/cos-key/key$|^/home/node/\.openclaw/|^/home/node/\.config/openclaw/|^/minimoi/repo/|^/minimoi/scripts/[^/]+\.(sh|py)$' || true)
  docker exec "$AGENT" sh -c 'test -S /var/run/docker.sock' 2>/dev/null && found="$found"$'\n'"/var/run/docker.sock"
  # With CoS's own key, the master key must not be in the container's environment at all (only its length is read).
  local n
  n=$(docker inspect --format '{{range .Config.Env}}{{if eq (index (split . "=") 0) "MINIMOI_MODEL_GATEWAY_KEY"}}{{len .}}{{end}}{{end}}' "$AGENT")
  [[ -s "$KEY_FILE" && "${n:-0}" -gt 26 ]] && found="$found"$'\n'"environment: MINIMOI_MODEL_GATEWAY_KEY (the master key) is set"
  if [[ -n "${found//[$'\n ']/}" ]]; then
    echo "credential-like files CoS can read:"; echo "$found" | sed '/^$/d'
    return 1
  fi
  echo "audit: no credential-like file is readable by CoS (its own key file and OpenClaw's own state excepted)"
}

case "${1:-}" in
  prepare) prepare ;;
  key) if [[ "${2:-}" == off ]]; then key_off; else key_on "${2:-}"; fi ;;
  status) status ;;
  audit) audit ;;
  vault) vault "${2:-}" ;;
  *) die "usage: cos_prod.sh prepare | key <dollars> | key off | vault on|off | status | audit" ;;
esac
