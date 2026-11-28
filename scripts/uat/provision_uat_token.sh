#!/usr/bin/env bash
# Create (or rotate) the staging UAT sign-in token (SPEC_AUTOMATIC_UAT §2). Run on the staging host by its operator.
#   1. 32 random bytes as 64 hex -> SSM SecureString /minimoi/staging/uat_signin_token (overwrites: a rotation)
#   2. its sha256 -> MINIMOI_UAT_SIGNIN_SHA256 in the staging env file (the token itself is never written to disk)
#   3. recreate the portal so it reads the new hash.
# The token is never printed. Production is refused: this script exits unless the running portal says
# MINIMOI_ROLE=standby with BASE_URL https://dev.minimoi.ai (the staging host's compose sets both).
set -euo pipefail
set +x
ENV_FILE="${UAT_ENV_FILE:-/opt/minimoi-staging/.env}"
SSM_NAME="${UAT_SSM_NAME:-/minimoi/staging/uat_signin_token}"
[ "$(docker exec minimoi-portal printenv MINIMOI_ROLE 2>/dev/null)" = "standby" ] \
  && [ "$(docker exec minimoi-portal printenv BASE_URL 2>/dev/null)" = "https://dev.minimoi.ai" ] \
  && [ -f "$ENV_FILE" ] || { echo "refusing: this is not the staging host (portal role/base URL, or $ENV_FILE missing)" >&2; exit 2; }
token="$(openssl rand -hex 32)"
aws ssm put-parameter --name "$SSM_NAME" --type SecureString --overwrite --value "$token" >/dev/null
hash="$(printf '%s' "$token" | sha256sum | cut -c1-64)"
unset token
tmp="$(mktemp)"; grep -v '^MINIMOI_UAT_SIGNIN_SHA256=' "$ENV_FILE" > "$tmp" || true
echo "MINIMOI_UAT_SIGNIN_SHA256=$hash" >> "$tmp"
cat "$tmp" > "$ENV_FILE"; rm -f "$tmp"
echo "UAT token stored in SSM ($SSM_NAME); hash written to $ENV_FILE. Recreate the portal to apply."
