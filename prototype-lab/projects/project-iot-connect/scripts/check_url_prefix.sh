#!/bin/sh
# Live URL-prefix check against a running IoT Connect.
#   sh scripts/check_url_prefix.sh http://127.0.0.1:8095 /app/iotconnect
#   sh scripts/check_url_prefix.sh http://127.0.0.1:8095 ""          (default: no prefix, no meta)
# Fetches openapi.json, every server-rendered page and every script under the
# prefix and fails if any root-absolute /static, /api/v1, href="/ or src="/
# reference lacks the prefix. Exit 1 on any failure.
set -u
base="${1:?base url}"; prefix="${2-}"
status=0
pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; status=1; }
tmp=$(mktemp "${TMPDIR:-/tmp}/iotconnect-prefix.XXXXXX")

pages="/ /presentation /admin /workbench /artifacts/invoice-comparison /artifacts/statement /portal /portal/subscriptions /portal/actions /portal/billing /operator /operator/accounts /operator/accounts/new /operator/account /operator/account/configuration /operator/subscriptions /operator/actions /operator/billing /operator/inventory /operator/bill-cycles /operator/catalog /operator/api-activity"
scripts="/static/api-client.js /static/iotconnect/iotconnect.js /static/admin.js /static/demo.js /static/invoice-comparison.js /static/statement.js"

code=$(curl -sS -o "$tmp" -w '%{http_code}' "$base$prefix/openapi.json")
if [ "$code" = "200" ] && grep -q "\"servers\":\[{\"url\":\"$prefix\"}\]" "$tmp"; then pass "openapi.json servers entry is \"$prefix\""
elif [ -z "$prefix" ] && [ "$code" = "200" ] && ! grep -q '"servers"' "$tmp"; then pass "openapi.json has no servers entry (no prefix)"
else fail "openapi.json servers entry (HTTP $code): $(head -c 160 "$tmp")"; fi
code=$(curl -sS -o /dev/null -w '%{http_code}' "$base$prefix/docs"); [ "$code" = "200" ] && pass "$prefix/docs → 200" || fail "$prefix/docs → $code"
code=$(curl -sS -o /dev/null -w '%{http_code}' "$base$prefix/api/v1/health"); [ "$code" = "200" ] && pass "$prefix/api/v1/health → 200" || fail "$prefix/api/v1/health → $code"
code=$(curl -sS -o /dev/null -w '%{http_code}' "$base$prefix/presentation/assets/slide-1.png"); [ "$code" = "200" ] && pass "$prefix/presentation/assets/slide-1.png → 200" || fail "slide-1.png → $code"
# Mounted static files resolve only under the prefixed path (root-path contract:
# the proxy must forward the full prefixed path).
for asset in /static/iotconnect/iotconnect.css /static/styles.css /static/api-client.js /fixtures/aster_5_subscriptions.csv; do
  code=$(curl -sS -o /dev/null -w '%{http_code}' "$base$prefix$asset"); [ "$code" = "200" ] && pass "$prefix$asset → 200 (mount exercised via the prefixed path)" || fail "$prefix$asset → $code"
done
if [ -n "$prefix" ]; then
  code=$(curl -sS -o /dev/null -w '%{http_code}' "$base/static/iotconnect/iotconnect.css"); [ "$code" = "404" ] && pass "unprefixed /static/iotconnect/iotconnect.css → 404 (documented contract)" || fail "unprefixed static → $code (expected 404)"
fi
# /project-design renders escaped Markdown prose; only its server-built header link is checked
code=$(curl -sS -o "$tmp" -w '%{http_code}' "$base$prefix/project-design")
if [ "$code" = "200" ] && grep -q "href=\"$prefix/\">← Project home" "$tmp"; then pass "$prefix/project-design header link prefixed"; else fail "$prefix/project-design header link (HTTP $code)"; fi

# occurrences of root-absolute references that do not start with the prefix
unprefixed() {  # stdin: document; prints offending references
  grep -oE '(href|src|action)="/[^"]*"|["'"'"'`]/static/[^"'"'"'`]*|["'"'"'`]/api/v1[^"'"'"'`]*' \
    | { if [ -n "$prefix" ]; then grep -vE "^(href|src|action)=\"$prefix(/|\")|^[\"'\`]$prefix/"; else cat; fi; }
}

for page in $pages; do
  code=$(curl -sS -o "$tmp" -w '%{http_code}' "$base$prefix$page")
  [ "$code" = "200" ] || { fail "page $page → HTTP $code"; continue; }
  if [ -n "$prefix" ]; then
    grep -q "<meta name=\"iotconnect-root-path\" content=\"$prefix\">" "$tmp" || fail "page $page lacks the root-path meta"
    bad=$(unprefixed < "$tmp" | sort -u)
    [ -z "$bad" ] && pass "page $page: all root-absolute references prefixed" || fail "page $page has unprefixed references: $(echo "$bad" | head -5 | tr '\n' ' ')"
  else
    grep -q 'iotconnect-root-path' "$tmp" && fail "page $page carries a root-path meta without a prefix" || pass "page $page: served without prefix meta"
  fi
done
for script in $scripts; do
  code=$(curl -sS -o "$tmp" -w '%{http_code}' "$base$prefix$script")
  [ "$code" = "200" ] || { fail "script $script → HTTP $code"; continue; }
  bad=$(grep -oE '["'"'"'`]/static/[^"'"'"'`]*|["'"'"'`]/api/v1[^"'"'"'`]*|(href|src)="/[^"]*"' "$tmp" | sort -u)
  [ -z "$bad" ] && pass "script $script: no hardcoded root-absolute /static, /api/v1, href=\"/ or src=\"/" || fail "script $script: $(echo "$bad" | head -3 | tr '\n' ' ')"
done
rm -f "$tmp"
[ $status -eq 0 ] && echo "URL PREFIX CHECK (${prefix:-no prefix}): PASS" || echo "URL PREFIX CHECK (${prefix:-no prefix}): FAIL"
exit $status
