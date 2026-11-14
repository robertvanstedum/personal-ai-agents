#!/bin/bash
# MC's read-only copy of the private planning repository (#587). Usage: refresh | rules | install ec2|dev | remove ec2|dev
# Layout: $ROOT/git (root-only git data, never mounted) and $ROOT/tree (files; MC mounts it read-only at /minimoi/planning)
# with tree/PLANNING_STATUS.txt "status=ok|skipped commit= fetched=". Kept short: it rides in the deploy's size-limited
# host-scripts bundle (tests/test_host_script_parity.py). Design and Dev steps: issue #587 and its PR.
# The token is read from SSM per run and reaches git only as an environment variable: never argv, config, URL or file.
set -euo pipefail
umask 022
ROOT="${MINIMOI_PLANNING_ROOT:-/opt/minimoi/planning}"
REMOTE="${MINIMOI_PLANNING_REMOTE:-https://github.com/robertvanstedum/minimoi-planning.git}"
BRANCH="${MINIMOI_PLANNING_BRANCH:-main}"
PARAM="${MINIMOI_PLANNING_TOKEN_PARAM:-/minimoi/production/planning_read_token}"
REGION="${AWS_REGION:-us-east-1}"
GD="$ROOT/git"; TREE="$ROOT/tree"; SN=PLANNING_STATUS.txt; STATUS="$TREE/$SN"; IX=PLANNING_INDEX.txt
ETC="${MINIMOI_ETC:-/etc}"; SCTL="${SYSTEMCTL:-systemctl}"
unset GIT_TRACE GIT_TRACE_CURL GIT_TRACE_PACKET GIT_CURL_VERBOSE GIT_TRACE2 GIT_TRACE2_EVENT GIT_TRACE2_PERF
export GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/bin/false
H=""; LAST=""

rules() {  # non-cone, gitignore style: everything, minus the answer key and career material (any case), and the
  # binaries MC's host reader refuses anyway (host_binary_refused): images, archives, PDFs (~380 of 530 MB, Nov 2026)
  printf '%s\n' '/*' '!scorers-only/' '!*[Cc][Aa][Rr][Ee][Ee][Rr]*' '!*[Rr][Ee][Ss][Uu][Mm][Ee]*' \
    '!*[Cc][Oo][Vv][Ee][Rr]-[Ll][Ee][Tt][Tt][Ee][Rr]*' '!*.[Pp][Nn][Gg]' '!*.[Jj][Pp][Gg]' '!*.[Jj][Pp][Ee][Gg]' \
    '!*.[Gg][Ii][Ff]' '!*.[Ww][Ee][Bb][Pp]' '!*.[Zz][Ii][Pp]' '!*.[Pp][Dd][Ff]' '!*.[Gg][Zz]' '!*.[Tt][Gg][Zz]' \
    '!*.[Mm][Pp]4' '!*.[Mm][Oo][Vv]'
}
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
g() { GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=http.https://github.com/.extraheader GIT_CONFIG_VALUE_0="$H" \
      git --git-dir="$GD" --work-tree="$TREE" "$@"; }
put() {  # status line, atomically
  mkdir -p "$TREE"; printf '%s\n' "$1" > "$TREE/.$SN.tmp"; chmod 0644 "$TREE/.$SN.tmp"; mv -f "$TREE/.$SN.tmp" "$STATUS"
  echo "planning-mirror: $1"
}
skip() {
  H=""
  put "status=skipped commit=$(g rev-parse -q --verify HEAD 2>/dev/null || echo none) fetched=${LAST:-never} attempted=$(now) reason=$1"
  exit 1
}

refresh() {
  [[ -f "$STATUS" ]] && LAST="$(sed -n 's/.*fetched=\([^ ]*\).*/\1/p' "$STATUS" | head -n 1)"
  [[ "$LAST" == never ]] && LAST=""
  install -d -m 0755 "$ROOT" "$TREE"
  if command -v flock >/dev/null; then exec 9>"$ROOT/.lock"; flock -n 9 || exit 0; fi
  if [[ ! -f "$GD/HEAD" ]]; then
    git init -q --bare "$GD" && git --git-dir="$GD" config core.bare false || skip init_failed
  fi
  chmod 0700 "$GD"
  local k v
  while read -r k v; do git --git-dir="$GD" config "$k" "$v"; done <<EOF
core.worktree $TREE
remote.origin.url $REMOTE
remote.origin.fetch +refs/heads/$BRANCH:refs/remotes/origin/$BRANCH
remote.origin.promisor true
remote.origin.partialclonefilter blob:none
core.sparseCheckout true
core.sparseCheckoutCone false
gc.auto 0
EOF
  mkdir -p "$GD/info"; printf '/%s\n/.%s.tmp\n' "$SN" "$SN" "$IX" "$IX" > "$GD/info/exclude"
  local want re=0
  want="$(rules)"
  if [[ "$(cat "$GD/info/sparse-checkout" 2>/dev/null)" != "$want" ]]; then
    printf '%s\n' "$want" > "$GD/info/sparse-checkout"; re=1
  fi
  if [[ "$REMOTE" == https://* ]]; then
    local t
    t="$(aws ssm get-parameter --region "$REGION" --name "$PARAM" --with-decryption --query Parameter.Value --output text 2>/dev/null)" || t=""
    [[ -n "$t" && "$t" != None ]] || skip token_unavailable
    H="AUTHORIZATION: basic $(printf 'x-access-token:%s' "$t" | base64 | tr -d '\n')"  # printf is a builtin: no argv
    unset t
  fi
  g fetch -q --no-tags --filter=blob:none origin || skip fetch_failed
  local new cur
  new="$(g rev-parse -q --verify "refs/remotes/origin/$BRANCH")" || skip no_remote_head
  cur="$(g rev-parse -q --verify HEAD 2>/dev/null || true)"
  if [[ "$new" != "$cur" ]]; then g checkout -q -f --detach "$new" || skip checkout_failed
  elif [[ "$re" == 1 ]]; then g read-tree -mu HEAD || skip reapply_failed; fi
  H=""
  index
  put "status=ok commit=$new fetched=$(now)"
}

index() {  # MC's starting point (#608): each NOW.md with its last commit date, newest first; no blob is fetched
  local f
  { echo "# NOW.md files, newest first (date of last change, path)"
    (cd "$TREE" && find initiatives operations -name NOW.md 2>/dev/null || true) | while read -r f; do
      printf '%s %s\n' "$(g log -1 --format=%cs -- ":(top)$f")" "$f"; done | sort -r; } > "$TREE/.$IX.tmp"
  chmod 0644 "$TREE/.$IX.tmp"; mv -f "$TREE/.$IX.tmp" "$TREE/$IX"
}

install_ec2() {
  local log=/opt/minimoi/logs/planning-mirror.log
  mkdir -p "$ETC/cron.d" "$ETC/logrotate.d"
  printf '%s\n' "# minimoi-planning-mirror (#587): from scripts/operations/planning_mirror.sh; edit the repo, not this file" \
    "*/5 * * * * root /opt/minimoi/scripts/planning_mirror.sh refresh >> $log 2>&1" > "$ETC/cron.d/.mpm.tmp"
  chmod 0644 "$ETC/cron.d/.mpm.tmp"; mv -f "$ETC/cron.d/.mpm.tmp" "$ETC/cron.d/minimoi-planning-mirror"
  printf '%s {\n weekly\n rotate 4\n compress\n missingok\n notifempty\n copytruncate\n}\n' "$log" > "$ETC/logrotate.d/minimoi-planning-mirror"
  echo "installed $ETC/cron.d/minimoi-planning-mirror"
}

install_dev() {
  local u="$ETC/systemd/system" d="${MINIMOI_PLANNING_SCRIPT_DIR:-/opt/minimoi-staging/scripts}"
  local r="${MINIMOI_PLANNING_ROOT:-/opt/minimoi-staging/planning}"
  mkdir -p "$u" "$d"
  cp "${BASH_SOURCE[0]}" "$d/.pm.tmp" && chmod 0755 "$d/.pm.tmp" && mv -f "$d/.pm.tmp" "$d/planning_mirror.sh"
  printf '[Unit]\nDescription=MiniMoi planning copy for MC (#587)\nAfter=network-online.target\n[Service]\nType=oneshot\nEnvironment=MINIMOI_PLANNING_ROOT=%s\nEnvironment=MINIMOI_PLANNING_TOKEN_PARAM=%s\nEnvironment=AWS_REGION=%s\nExecStart=%s/planning_mirror.sh refresh\n' \
    "$r" "$PARAM" "$REGION" "$d" > "$u/minimoi-planning-mirror.service"
  printf '[Unit]\nDescription=MiniMoi planning copy, every 5 minutes\n[Timer]\nOnBootSec=1min\nOnCalendar=*:0/5\nPersistent=true\n[Install]\nWantedBy=timers.target\n' \
    > "$u/minimoi-planning-mirror.timer"
  "$SCTL" daemon-reload; "$SCTL" enable --now minimoi-planning-mirror.timer
  echo "installed the Dev planning timer ($r)"
}

case "${1:-} ${2:-}" in
  "refresh "*) refresh ;;
  "rules "*) rules ;;
  "install ec2") install_ec2 ;;
  "install dev") install_dev ;;
  "remove ec2") rm -f "$ETC/cron.d/minimoi-planning-mirror" "$ETC/logrotate.d/minimoi-planning-mirror" ;;
  "remove dev") "$SCTL" disable --now minimoi-planning-mirror.timer || true
                rm -f "$ETC/systemd/system/minimoi-planning-mirror."{timer,service}; "$SCTL" daemon-reload ;;
  *) echo "usage: $0 refresh | rules | install ec2|dev | remove ec2|dev" >&2; exit 2 ;;
esac
