#!/bin/bash
# voice.sh — the voice summary's two capped gateway keys on dev (Spec 165 §5.4,
# decision D22 (a)): when a voice session ends, its summary is one plain
# completion through the gateway, on a key of its own per host. Robert runs
# `voice.sh summary-keys` himself; an agent never does.
#
# Usage:
#   voice.sh summary-keys   asks everything first: for each key, voice-summary-mc
#                           (Master Craftsman, mc.env) and voice-summary-cos
#                           (Chief of Staff, cos.env), whether to rotate an
#                           existing one, and its monthly cap in dollars
#                           (default 5). Then:
#                           1. the LIVE gateway config
#                              (~/minimoi-staging/config/litellm.staging.yaml,
#                              not the repo file) gets the routes
#                              minimoi-voice-summary and minimoi-voice-intent
#                              (confirm by voice, v3: the understanding call,
#                              under the same keys) where it lacks them: exactly
#                              this checkout's blocks, with one timestamped backup
#                              beside it; then ONLY the gateway container restarts
#                              (CoS's and MC's model calls fail for about a
#                              minute). The backup goes back if the gateway is
#                              not healthy on the new file;
#                           2. each key is made inside the gateway: the two
#                              aliases only, chat completions only, a monthly budget,
#                              rpm 60; written unprinted (600) as
#                              MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_MC to mc.env and
#                              MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_COS to cos.env.
#                              A rotated key's old one is deleted after the new
#                              one exists;
#                           3. dev's voice switch-over runs again
#                              (operators/dev-voice-1-2-20261029/deploy.sh), so the
#                              portal and cos-scheduler get their key by compose
#                              interpolation (both are recreated).
#                           Prints only the caps, models, routes, rpm and the
#                           last 4 characters of each key's id.
#   voice.sh summary-status read-only: whether each key is in its env file (never
#                           the value), whether the live gateway config and the
#                           running gateway have the route, and whether the
#                           running portal and cos-scheduler have their key
#                           variable (set or not set).
#
# Needs the gateway's key database (mc.sh gateway-keys) and reuses staging's
# existing provider keys (nothing new from a provider). Without its key, a
# host's summary reads "unavailable", as before.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
source "$(dirname "${BASH_SOURCE[0]}")/mc_keys.sh"

GATEWAY_CONTAINER=minimoi-model-gateway
VOICE_ALIAS=minimoi-voice-summary
# Every voice route the keys reach (VOICE_SUMMARY_KEY_MODELS), the summary's first.
VOICE_ALIASES=("$VOICE_ALIAS" minimoi-voice-intent)
VOICE_DEFAULT_CAP=5
# The live gateway's model map: build.sh copies it from the release, and
# docker-compose.staging.yml mounts it over the image's /app/config.yaml.
STAGING_GATEWAY_CONFIG="$STAGING_ROOT/config/litellm.staging.yaml"
# The route's block comes from this checkout (the branch that carries the route).
VOICE_ROUTE_SOURCE="${STAGING_VOICE_ROUTE_SOURCE:-$STAGING_SCRIPTS_DIR/../../services/model_gateway/litellm.staging.yaml}"
# Dev's voice switch-over (outside the repo); its overlays pass both keys by
# interpolation from mc.env and cos.env.
VOICE_DEPLOY="${STAGING_VOICE_DEPLOY:-$HOME/minimoi-runtime/operators/dev-voice-1-2-20261029/deploy.sh}"

voice_env_file() { case "$1" in mc) echo "$STAGING_MC_ENV" ;; cos) echo "$STAGING_COS_ENV" ;; esac; }
voice_var() { case "$1" in mc) echo MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_MC ;; cos) echo MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_COS ;; esac; }
voice_container() { case "$1" in mc) echo minimoi-portal ;; cos) echo minimoi-cos-scheduler ;; esac; }
voice_who() { case "$1" in mc) echo "Master Craftsman" ;; cos) echo "Chief of Staff" ;; esac; }

# env_value FILE NAME: a variable's value from an env file, for comparison only.
env_value() { sed -n "s/^$2=//p" "$1" 2>/dev/null | tail -n 1 | sed "s/^'\\(.*\\)'\$/\\1/; s/^\"\\(.*\\)\"\$/\\1/"; }
health_of() { docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$1" 2>/dev/null || echo absent; }
sha_of() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1"; else shasum -a 256 "$1"; fi | awk '{print $1}'; }

# has_route FILE [ALIAS]: does this gateway config route that alias (the summary's when none is named)?
route_re() { echo "^[[:space:]]*-[[:space:]]+model_name:[[:space:]]*${1:-$VOICE_ALIAS}[[:space:]]*\$"; }
ROUTE_RE=$(route_re "$VOICE_ALIAS")
has_route() { grep -Eq "$(route_re "${2:-$VOICE_ALIAS}")" "$1" 2>/dev/null; }
has_routes() { local a; for a in "${VOICE_ALIASES[@]}"; do has_route "$1" "$a" || return 1; done; }

# deploy_has TEXT: does the switch-over script contain TEXT, literally?
deploy_has() { grep -Fq "$1" "$VOICE_DEPLOY"; }

# file_has_var FILE NAME: is NAME set to something in FILE? (exit status only)
file_has_var() { grep -Eq "^$2=['\"]?[^'\"[:space:]]" "$1" 2>/dev/null; }

# container_var_state CONTAINER NAME: "set", "not set" or "not running", from
# the running container's own definition. The template prints "set" or
# nothing: never the value.
container_var_state() {
  local running got
  running=$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null || true)
  [[ "$running" == true ]] || { echo "not running"; return 0; }
  got=$(docker inspect -f "{{range .Config.Env}}{{\$p := split . \"=\"}}{{if eq (index \$p 0) \"$2\"}}{{if ne . \"$2=\"}}set{{end}}{{end}}{{end}}" "$1" 2>/dev/null || true)
  if [[ "$got" == set ]]; then echo set; else echo "not set"; fi
}

# Refuses when a summary key is missing, equals another credential, or sits in
# .env, which every main service loads whole (values never printed).
voice_key_problems() {
  local host="$1" var file key pair problems=""
  var=$(voice_var "$host"); file=$(voice_env_file "$host")
  key=$(env_value "$file" "$var")
  [[ -n "$key" ]] || problems="$problems $var is missing from $(basename "$file");"
  if [[ -n "$key" ]]; then
    for pair in "$STAGING_ENV_FILE|MINIMOI_MODEL_GATEWAY_KEY" "$STAGING_MC_ENV|MC_MODEL_GATEWAY_KEY" \
                "$STAGING_COS_ENV|COS_MODEL_GATEWAY_KEY" "$STAGING_MC_ENV|$(voice_var mc)" "$STAGING_COS_ENV|$(voice_var cos)"; do
      [[ "${pair##*|}" != "$var" ]] || continue
      [[ "$key" != "$(env_value "${pair%|*}" "${pair##*|}")" ]] || problems="$problems $var equals ${pair##*|};"
    done
  fi
  if grep -Eq "^$var=" "$STAGING_ENV_FILE" 2>/dev/null; then
    problems="$problems $var is in .env (every main service loads it; use $(basename "$file"));"
  fi
  echo "${problems# }"
}

# voice_route_py ALIAS: Python run INSIDE the gateway container (stdin; the
# gateway's own YAML parser). Env in: VOICE_LIVE_CFG (the live config's text),
# VOICE_ROUTE_SRC (the repo file's text). Prints the live config with exactly
# the repo's route block for ALIAS added (its comment lines too), re-indented
# to the live file's style, before the first route that follows it in the repo
# and is in the live file (else after the last route). Refuses unless the
# result parses to the live config plus that one route and nothing else. The
# config holds no secret (provider keys are os.environ/ references).
voice_route_py() {
  printf 'ALIAS = "%s"\n' "$1"
  cat <<'PY'
import copy, os, sys, yaml
ITEM = "- model_name: "
live, src = os.environ["VOICE_LIVE_CFG"], os.environ["VOICE_ROUTE_SRC"]
def fail(why):
    print("voice summary route: " + why, file=sys.stderr)
    sys.exit(2)
def indent(line):
    return len(line) - len(line.lstrip(" "))
old, repo = yaml.safe_load(live), yaml.safe_load(src)
names = [r.get("model_name") for r in old.get("model_list") or []]
order = [r.get("model_name") for r in repo.get("model_list") or []]
NEXT = next((n for n in order[order.index(ALIAS) + 1:] if n in names), None) if ALIAS in order else None
if ALIAS in names:
    fail("the live config already has it")
want = [r for r in repo.get("model_list") or [] if r.get("model_name") == ALIAS]
if len(want) != 1 or not str((want[0].get("litellm_params") or {}).get("api_key", "")).startswith("os.environ/"):
    fail("the repository config has no single route for it taking its provider key from the environment")
src_lines = src.splitlines(True)
at = [k for k, l in enumerate(src_lines) if l.strip() == ITEM + ALIAS]
if len(at) != 1:
    fail("the repository config's route block was not found")
base = indent(src_lines[at[0]])
start, end = at[0], at[0] + 1
while start > 0 and src_lines[start - 1].lstrip().startswith("#") and indent(src_lines[start - 1]) == base:
    start -= 1
while end < len(src_lines) and (not src_lines[end].strip() or indent(src_lines[end]) > base):
    end += 1
block = src_lines[start:end]
while block and not block[-1].strip():
    block.pop()
lines = live.splitlines(True)
if lines and not lines[-1].endswith("\n"):
    lines[-1] += "\n"
items = [k for k, l in enumerate(lines) if l.lstrip().startswith(ITEM)]
if not items:
    fail("the live config has no routes")
live_base = indent(lines[items[0]])
shift = live_base - base
moved = []
for l in block:
    if not l.strip():
        moved.append(l)
    elif shift >= 0:
        moved.append(" " * shift + l)
    elif indent(l) >= -shift:
        moved.append(l[-shift:])
    else:
        fail("the route block cannot be re-indented to the live file's style")
nxt = [k for k in items if NEXT and lines[k].strip() == ITEM + NEXT and indent(lines[k]) == live_base]
if nxt:
    pos = nxt[0]
    while pos > 0 and lines[pos - 1].lstrip().startswith("#") and indent(lines[pos - 1]) == live_base:
        pos -= 1
    gap = ["\n"] if pos > 0 and not lines[pos - 1].strip() else []
    lines[pos:pos] = moved + gap
    index = names.index(NEXT)
else:
    pos = items[-1] + 1
    while pos < len(lines) and (not lines[pos].strip() or indent(lines[pos]) > live_base):
        pos += 1
    while pos > items[-1] + 1 and not lines[pos - 1].strip():
        pos -= 1
    lines[pos:pos] = moved
    index = len(names)
new = "".join(lines)
expected = copy.deepcopy(old)
expected["model_list"].insert(index, want[0])
if yaml.safe_load(new) != expected:
    fail("the result is not the live config plus exactly that route")
sys.stdout.write(new)
PY
}

# The gateway reads its config only at start, from a bind-mounted file: a
# restart (not a compose recreate) picks the new file up and keeps the
# container's definition exactly as it is. Only the gateway restarts. True when
# it is healthy again and reads exactly the file on disk.
restart_gateway() {
  local i=0 want got
  note "restarting ONLY the model gateway (CoS's and MC's model calls fail for about a minute)"
  docker restart --time 20 "$GATEWAY_CONTAINER" >/dev/null || return 1
  until [[ "$(health_of "$GATEWAY_CONTAINER")" == healthy ]]; do
    i=$((i + 5)); [[ "$i" -lt 240 ]] || return 1
    sleep 5
  done
  want=$(sha_of "$STAGING_GATEWAY_CONFIG")
  got=$(docker exec "$GATEWAY_CONTAINER" sha256sum /app/config.yaml 2>/dev/null | awk '{print $1}')
  [[ -n "$want" && "$want" == "$got" ]]
}

# The live gateway config gets each voice route (VOICE_ALIASES) it lacks, in
# one change: one backup, then one gateway restart.
ensure_route() {
  local cfg="$STAGING_GATEWAY_CONFIG" backup tmp next rc alias added=""
  for alias in "${VOICE_ALIASES[@]}"; do
    if has_route "$cfg" "$alias"; then
      note "the live gateway config already routes $alias (unchanged)"
    fi
  done
  if has_routes "$cfg"; then
    note "the gateway is not restarted"
    return 0
  fi
  tmp="$cfg.voice-tmp.$$"
  cp -p "$cfg" "$tmp"
  for alias in "${VOICE_ALIASES[@]}"; do
    has_route "$tmp" "$alias" && continue
    rc=0
    next="$tmp.next"
    voice_route_py "$alias" | VOICE_LIVE_CFG=$(cat "$tmp") VOICE_ROUTE_SRC=$(cat "$VOICE_ROUTE_SOURCE") \
      docker exec -i -e VOICE_LIVE_CFG -e VOICE_ROUTE_SRC "$GATEWAY_CONTAINER" python - > "$next" || rc=$?
    if [[ "$rc" != 0 || ! -s "$next" ]] || ! has_route "$next" "$alias"; then
      rm -f "$tmp" "$next"
      die "could not add $alias to the live gateway config (nothing changed)"
    fi
    mv -f "$next" "$tmp"
    added="$added $alias"
  done
  backup="$cfg.before-voice-summary.$(date -u +%Y%m%dT%H%M%SZ)"
  cp -p "$cfg" "$backup"
  chmod "$(file_mode "$cfg")" "$tmp"               # build.sh's mode: the gateway reads it as its own user
  mv -f "$tmp" "$cfg"
  note "added${added} to $cfg (backup: $backup)"
  if ! restart_gateway; then
    cp -p "$backup" "$cfg"
    restart_gateway || true
    die "the gateway was not healthy on the new config: put the backup back and restarted it (no key was made)"
  fi
  note "the gateway is healthy and reads the new routes"
}

# make_key HOST CAP: makes that host's key inside the gateway and writes it,
# unprinted, to its env file.
make_key() {
  local host="$1" cap="$2" file var who old out key last4 tmp
  file=$(voice_env_file "$host"); var=$(voice_var "$host"); who=$(voice_who "$host")
  old=$(env_value "$file" "$var")
  out=$(voice_summary_keygen_py "$host" | VOICE_OLD_KEY="$old" VOICE_CAP="$cap" \
        docker exec -i -e VOICE_OLD_KEY -e VOICE_CAP "$GATEWAY_CONTAINER" python -) \
    || die "the gateway did not make $who's voice summary key (nothing written for it)"
  old=""
  key=$(sed -n 1p <<< "$out")
  last4=$(sed -n 2p <<< "$out")
  out=""
  [[ "$key" == sk-* ]] || die "the gateway's answer for $who was not a key (nothing written for it)"
  tmp="$file.tmp.$$"
  { grep -v "^$var=" "$file" || true; printf '%s=%s\n' "$var" "$key"; } > "$tmp"
  chmod 600 "$tmp"; mv -f "$tmp" "$file"
  key=""
  report="$report$who's voice summary key (voice-summary-$host): monthly cap \$$cap ($VOICE_SUMMARY_KEY_BUDGET_DURATION), models $VOICE_SUMMARY_KEY_MODELS, routes $VOICE_SUMMARY_KEY_ROUTES, rpm $VOICE_SUMMARY_KEY_RPM, key id …$last4"$'\n'
}

summary_keys() {
  local host file var who answer cap cap_mc="" cap_cos="" todo="" problems redeploy=0
  report=""
  require_absolute_root
  gateway_keys_on || die "the gateway has no key database yet: run mc.sh gateway-keys first"
  [[ "$(health_of "$GATEWAY_CONTAINER")" == healthy ]] || die "the model gateway is not healthy"
  [[ -f "$STAGING_GATEWAY_CONFIG" ]] || die "missing $STAGING_GATEWAY_CONFIG (build.sh writes it)"
  has_routes "$VOICE_ROUTE_SOURCE" || die "$VOICE_ROUTE_SOURCE has no ${VOICE_ALIASES[*]} routes (run voice.sh from the branch that carries them)"
  [[ -x "$VOICE_DEPLOY" ]] || die "missing $VOICE_DEPLOY (dev's voice switch-over)"
  for host in mc cos; do
    var=$(voice_var "$host")
    # Its overlay names the variable by interpolation ("\${" in its unquoted heredoc, or "${" in a quoted one).
    deploy_has "\"$var\": \"\\\${$var:-}\"" || deploy_has "\"$var\": \"\${$var:-}\"" \
      || die "$VOICE_DEPLOY does not pass $var to $(voice_container "$host") yet (nothing changed)"
    if grep -Eq "^$var=" "$STAGING_ENV_FILE" 2>/dev/null; then
      die "$var is in .env (every main service loads it whole); remove it first (nothing changed)"
    fi
  done
  umask 077                                   # the keys' temp files are never world-readable, even briefly
  touch "$STAGING_MC_ENV" "$STAGING_COS_ENV"; chmod 600 "$STAGING_MC_ENV" "$STAGING_COS_ENV"

  # Ask everything first, so a "no" or a bad cap changes nothing.
  for host in mc cos; do
    file=$(voice_env_file "$host"); var=$(voice_var "$host"); who=$(voice_who "$host")
    if [[ -n "$(env_value "$file" "$var")" ]]; then
      printf "%s's voice summary key already exists. Rotate it (the old key stops working)? [y/N] " "$who" >&2
      IFS= read -r answer || answer=""
      if [[ "$answer" != y && "$answer" != Y ]]; then note "keeping $who's voice summary key"; continue; fi
    fi
    printf "%s's voice summary monthly cap in dollars [%s]: " "$who" "$VOICE_DEFAULT_CAP" >&2
    IFS= read -r cap || cap=""
    cap=${cap:-$VOICE_DEFAULT_CAP}
    valid_cap "$cap" || die "'$cap' is not a dollar amount between 0 and 500 (nothing changed)"
    if [[ "$host" == mc ]]; then cap_mc=$cap; else cap_cos=$cap; fi
    todo="$todo $host"
  done

  ensure_route
  for host in $todo; do
    if [[ "$host" == mc ]]; then make_key mc "$cap_mc"; else make_key cos "$cap_cos"; fi
  done
  for host in mc cos; do
    problems=$(voice_key_problems "$host")
    [[ -z "$problems" ]] || die "$problems"
  done

  [[ -z "$todo" ]] || redeploy=1
  for host in mc cos; do
    [[ "$(container_var_state "$(voice_container "$host")" "$(voice_var "$host")")" == set ]] || redeploy=1
  done
  if [[ "$redeploy" == 1 ]]; then
    note "applying dev's voice switch-over again ($VOICE_DEPLOY): the portal and cos-scheduler are recreated with their keys"
    "$VOICE_DEPLOY" || die "$VOICE_DEPLOY failed (the keys are written; fix it and run it again)"
    for host in mc cos; do
      [[ "$(container_var_state "$(voice_container "$host")" "$(voice_var "$host")")" == set ]] \
        || die "$(voice_container "$host") does not have $(voice_var "$host") after the switch-over"
    done
  else
    note "the portal and cos-scheduler already have their keys (not recreated)"
  fi
  printf '%s' "$report"
  echo "portal: $(voice_var mc) set; cos-scheduler: $(voice_var cos) set; gateway routes ${VOICE_ALIASES[*]}"
  note "next: end a voice session with MC and one with CoS; each summary should read as a summary, not \"unavailable\""
}

summary_status() {
  local host file var state
  for host in mc cos; do
    file=$(voice_env_file "$host"); var=$(voice_var "$host")
    if [[ ! -f "$file" ]]; then
      echo "$var: absent ($file does not exist)"
    elif file_has_var "$file" "$var"; then
      echo "$var: present in $(basename "$file") (mode $(file_mode "$file"))"
    else
      echo "$var: absent from $(basename "$file")"
    fi
  done
  if has_route "$STAGING_GATEWAY_CONFIG"; then
    echo "live gateway config: routes $VOICE_ALIAS ($STAGING_GATEWAY_CONFIG)"
  else
    echo "live gateway config: no $VOICE_ALIAS route ($STAGING_GATEWAY_CONFIG)"
  fi
  state=$(health_of "$GATEWAY_CONTAINER")
  if [[ "$state" == absent ]]; then
    echo "running gateway: not running"
  elif docker exec "$GATEWAY_CONTAINER" grep -Eq "$ROUTE_RE" /app/config.yaml 2>/dev/null; then
    echo "running gateway: $state, reads $VOICE_ALIAS"
  elif has_route "$STAGING_GATEWAY_CONFIG"; then
    echo "running gateway: $state, no $VOICE_ALIAS (restart it: it reads the config only at start)"
  else
    echo "running gateway: $state, no $VOICE_ALIAS"
  fi
  for host in mc cos; do
    echo "$(voice_container "$host"): $(voice_var "$host") $(container_var_state "$(voice_container "$host")" "$(voice_var "$host")")"
  done
}

case "${1:-}" in
  summary-keys) summary_keys ;;
  summary-status) summary_status ;;
  *) die "usage: voice.sh summary-keys | summary-status" ;;
esac
