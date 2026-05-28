#!/bin/bash
# start_cloudflared.sh — Run Cloudflare tunnel using token from macOS Keychain.
# Called by com.vanstedum.cloudflared launchd agent.
# Token stored under: service=cloudflare, account=tunnel_token

TOKEN=$(security find-generic-password -s "cloudflare" -a "tunnel_token" -w 2>/dev/null)

if [ -z "$TOKEN" ]; then
  echo "ERROR: Could not read tunnel token from Keychain (service=cloudflare, account=tunnel_token)" >&2
  exit 1
fi

exec /opt/homebrew/bin/cloudflared tunnel run --token "$TOKEN"
