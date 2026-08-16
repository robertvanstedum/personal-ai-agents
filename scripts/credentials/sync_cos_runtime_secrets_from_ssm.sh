#!/usr/bin/env bash
set -euo pipefail

# Materialize the provider and runtime-to-runtime credentials required by the
# COS beta containers. Parameter Store remains authoritative; /opt/minimoi/.env
# is only the host-local Docker Compose projection and this script never prints
# secret values.
target_env="${1:-/opt/minimoi/.env}"
test -f "$target_env"

tmp_env=$(mktemp "${target_env}.cos-sync.XXXXXX")
cp "$target_env" "$tmp_env"

sync_secret() {
  key="$1"
  parameter="$2"
  secret_value=$(aws ssm get-parameter \
    --name "$parameter" \
    --with-decryption \
    --query 'Parameter.Value' \
    --output text)
  next_env=$(mktemp "${target_env}.cos-next.XXXXXX")
  grep -v "^${key}=" "$tmp_env" > "$next_env" || true
  printf '%s=%s\n' "$key" "$secret_value" >> "$next_env"
  mv "$next_env" "$tmp_env"
}

sync_secret COS_AGENT_A_GATEWAY_TOKEN /minimoi/production/cos_agent_a_gateway_token
sync_secret MINIMOI_MODEL_GATEWAY_KEY /minimoi/production/model_gateway_key
sync_secret MINIMOI_MODEL_GATEWAY_RECEIPT_KEY /minimoi/production/model_gateway_receipt_key
sync_secret XAI_API_KEY /minimoi/production/xai_api_key
sync_secret ANTHROPIC_API_KEY /minimoi/production/anthropic_api_key
sync_secret OPENAI_API_KEY /minimoi/production/openai_api_key

chmod --reference="$target_env" "$tmp_env"
chown --reference="$target_env" "$tmp_env"
mv "$tmp_env" "$target_env"
echo "COS runtime credentials synchronized from SSM."
