#!/bin/sh
# Live trusted-identity check.
#   sh scripts/check_proxy_auth.sh http://127.0.0.1:8095 /app/iotconnect minimoi_proxy
#   sh scripts/check_proxy_auth.sh http://127.0.0.1:8095 ""             local_demo
# Prints every curl call with its HTTP status. Exit 1 on any deviation.
set -u
base="${1:?base url}"; prefix="${2-}"; mode="${3:-local_demo}"
status=0
expect() {  # $1 label, $2 expected code, remaining: curl args (url last)
  label="$1"; want="$2"; shift 2
  got=$(curl -sS -o /dev/null -w '%{http_code}' "$@")
  if [ "$got" = "$want" ]; then echo "PASS  [$got] $label"; else echo "FAIL  [$got, expected $want] $label"; status=1; fi
}
admin_url="$base$prefix/api/v1/admin/legacy-accounts/available"
aster_id=$(curl -sS "$base$prefix/api/v1/accounts" | sed -n 's/.*"account_id": *"\([^"]*\)", *"account_number": *"ACCT-000100".*/\1/p' | head -1)
[ -n "$aster_id" ] || aster_id=$(curl -sS "$base$prefix/api/v1/accounts" | python3 -c 'import json,sys; print(next(r["account_id"] for r in json.load(sys.stdin) if r["account_number"]=="ACCT-000100"))' 2>/dev/null)
customer_url="$base$prefix/api/v1/accounts/$aster_id/activation-batches"
echo "mode=$mode prefix='${prefix}' admin=$admin_url"
if [ "$mode" = "minimoi_proxy" ]; then
  expect "owner X-Minimoi-* headers → admin GET"           200 -H 'X-Minimoi-User-Tier: owner' -H 'X-Minimoi-Username: demo-owner' -H 'X-Minimoi-Auth-Id: auth-0001' "$admin_url"
  expect "owner X-Minimoi-* headers → customer-scoped GET" 200 -H 'X-Minimoi-User-Tier: owner' -H 'X-Minimoi-Username: demo-owner' -H 'X-Minimoi-Auth-Id: auth-0001' "$customer_url"
  expect "no headers → admin GET"                          403 "$admin_url"
  expect "no headers → customer-scoped GET"                403 "$customer_url"
  expect "X-Demo-Role admin only (no X-Minimoi) → admin GET" 403 -H 'X-Demo-Role: BUSINESS_OPS_ADMIN' "$admin_url"
  expect "X-Demo customer headers only → customer-scoped GET" 403 -H 'X-Demo-Role: ENTERPRISE_CUSTOMER' -H "X-Demo-Account-ID: $aster_id" "$customer_url"
  for tier in guest admin family; do
    expect "tier=$tier + spoofed X-Demo-Role admin → admin GET" 403 -H "X-Minimoi-User-Tier: $tier" -H 'X-Demo-Role: BUSINESS_OPS_ADMIN' "$admin_url"
  done
  expect "empty tier header → admin GET"                   403 -H 'X-Minimoi-User-Tier;' "$admin_url"
  # Read-only proof only: this script runs inside `make verify` after evidence
  # capture, so it must not reset or mutate demo data.
  expect "owner tier → admin GET activation batches (admin-only route)" 200 -H 'X-Minimoi-User-Tier: owner' "$base$prefix/api/v1/admin/activation-batches"
  expect "non-owner tier → admin GET activation batches" 403 -H 'X-Minimoi-User-Tier: guest' "$base$prefix/api/v1/admin/activation-batches"
  body=$(curl -sS "$base$prefix/openapi.json")
  echo "$body" | grep -q '"name":"X-Minimoi-User-Tier","in":"header","required":true' && echo "PASS  openapi documents X-Minimoi-User-Tier as required" || { echo "FAIL  openapi does not document X-Minimoi-User-Tier as required"; status=1; }
  echo "$body" | grep -q '"name":"X-Demo-Role"' && { echo "FAIL  openapi still advertises X-Demo-Role in proxy mode"; status=1; } || echo "PASS  openapi does not advertise X-Demo-Role in proxy mode"
else
  expect "X-Demo-Role admin → admin GET"                    200 -H 'X-Demo-Role: BUSINESS_OPS_ADMIN' "$admin_url"
  expect "X-Demo customer headers → customer-scoped GET"    200 -H 'X-Demo-Role: ENTERPRISE_CUSTOMER' -H "X-Demo-Account-ID: $aster_id" "$customer_url"
  expect "no headers → admin GET (contract error)"          422 "$admin_url"
  expect "X-Minimoi owner only (ignored in local_demo) → admin GET" 422 -H 'X-Minimoi-User-Tier: owner' "$admin_url"
  expect "wrong demo role → admin GET"                      403 -H 'X-Demo-Role: ENTERPRISE_CUSTOMER' "$admin_url"
fi
[ $status -eq 0 ] && echo "PROXY AUTH CHECK ($mode): PASS" || echo "PROXY AUTH CHECK ($mode): FAIL"
exit $status
