#!/usr/bin/env bash
# Shared, testable pieces of the IoT Connect release path.
# Sourced by scripts/operations/deploy_iotconnect_ec2.sh; the tag workflow syncs
# this file to /opt/minimoi/iotconnect/lib/ next to the deploy script.
#
# Nothing here prints a secret value.

# ── release-tag grammar ──────────────────────────────────────────────────────
# Byte-identical to minimoi_portal.config.RELEASE_TAG_ERE (which is derived from
# RELEASE_TAG_PATTERN, the same grammar the portal uses with re.fullmatch).
IOTCONNECT_RELEASE_TAG_ERE='^iotconnect-v[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9]+(\.[A-Za-z0-9]+)*)?$'

validate_release_tag() {  # $1 candidate tag
  [[ "${1-}" =~ ^iotconnect-v[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9]+(\.[A-Za-z0-9]+)*)?$ ]]
}

# ── database-password contract (Codex release review 1, P1) ──────────────────
# Generation rule, also documented in Spec #154 §7 and the deploy-script header:
#
#   24–128 printable ASCII characters, no whitespace, quotes, or the characters
#   : @ / ? # % — e.g.
#     openssl rand -base64 48 | tr -d '/+=' | cut -c1-40
#
# Rationale: the value is written single-quoted into the Compose .env file (so
# `$` is literal to Compose) and interpolated into a PostgreSQL URI, where
# : @ / ? # % are delimiters and would silently change the parsed DSN.
validate_db_password() {  # $1 candidate password; returns 0 when acceptable
  local pw="${1-}"
  [ -n "$pw" ] || return 1
  local n=${#pw}
  [ "$n" -ge 24 ] && [ "$n" -le 128 ] || return 1
  # Only printable ASCII (0x21-0x7E): rejects space, tab, newline and controls.
  case "$pw" in
    *[!\!-\~]*) return 1 ;;
  esac
  # Single quote would break the single-quoted .env line.
  case "$pw" in
    *"'"*) return 1 ;;
  esac
  # URI delimiters that make the DSN ambiguous.
  case "$pw" in
    *[:@/?\#%]*) return 1 ;;
  esac
  return 0
}

# ── read-only hosted-mode smoke (Codex release review 1, P1) ─────────────────
# Runs against the deployed app in IOTCONNECT_AUTH_MODE=minimoi_proxy, which
# trusts only the portal-verified identity headers from
# prototype-lab/projects/project-iot-connect/app/dependencies.py:
#   X-Minimoi-User-Tier / X-Minimoi-Username / X-Minimoi-Auth-Id
# Every call is a GET. Only route + status is printed, never a header value.
hosted_smoke() {  # $1 base url, e.g. http://127.0.0.1:8095
  local base="${1:?base url}"
  local admin_route="/api/v1/admin/activation-batches"
  local rc=0 code body

  _smoke_report() {  # $1 label, $2 got, $3 want
    if [ "$2" = "$3" ]; then
      echo "PASS  [$2] $1"
    else
      echo "FAIL  [$2, expected $3] $1"
      rc=1
    fi
  }

  # (a) owner-tier identity reaches seeded account data
  body=$(curl -sS -o /tmp/iotconnect_smoke_accounts.json -w '%{http_code}' \
    -H 'X-Minimoi-User-Tier: owner' -H 'X-Minimoi-Username: deploy-smoke' \
    -H 'X-Minimoi-Auth-Id: deploy-smoke' "$base/api/v1/accounts" || echo 000)
  _smoke_report "owner tier GET /api/v1/accounts" "$body" 200
  if grep -q 'ACCT-000100' /tmp/iotconnect_smoke_accounts.json 2>/dev/null; then
    echo "PASS  seeded account ACCT-000100 present in /api/v1/accounts"
  else
    echo "FAIL  seeded account ACCT-000100 missing from /api/v1/accounts"; rc=1
  fi
  rm -f /tmp/iotconnect_smoke_accounts.json

  # (b) owner tier reaches an admin-only route
  code=$(curl -sS -o /dev/null -w '%{http_code}' -H 'X-Minimoi-User-Tier: owner' \
    "$base$admin_route" || echo 000)
  _smoke_report "owner tier GET $admin_route" "$code" 200

  # (c) no identity headers at all is denied
  code=$(curl -sS -o /dev/null -w '%{http_code}' "$base$admin_route" || echo 000)
  _smoke_report "no identity headers GET $admin_route" "$code" 403

  # (d) a non-owner tier is denied
  code=$(curl -sS -o /dev/null -w '%{http_code}' -H 'X-Minimoi-User-Tier: guest' \
    "$base$admin_route" || echo 000)
  _smoke_report "guest tier GET $admin_route" "$code" 403

  # (e) the hosted prefix is advertised
  if curl -sS "$base/openapi.json" | grep -q '"servers":\[{"url":"/app/iotconnect"}\]'; then
    echo "PASS  openapi.json servers entry is /app/iotconnect"
  else
    echo "FAIL  openapi.json servers entry is not /app/iotconnect"; rc=1
  fi

  [ "$rc" -eq 0 ] && echo "HOSTED SMOKE: PASS" || echo "HOSTED SMOKE: FAIL"
  return "$rc"
}
