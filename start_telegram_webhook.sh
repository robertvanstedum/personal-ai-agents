#!/bin/bash
# start_telegram_webhook.sh
# Starts the full webhook stack: webhook server + Cloudflare tunnel + Telegram registration
#
# ─── TUNNEL MODE ───────────────────────────────────────────────────────────────
# CURRENT:    Temporary tunnel (cloudflared quick tunnel)
#             - No Cloudflare account needed
#             - URL is random and changes on each restart
#             - Webhook auto-re-registers on every start (URL change is invisible)
#             - Good for MacBook development
#
# TODO (Mac Mini production): Switch to named tunnel for a stable URL
#   1. Run: cloudflared login            (browser auth, creates ~/.cloudflared/cert.pem)
#   2. Run: cloudflared tunnel create curator-bot
#   3. Run: cloudflared tunnel route dns curator-bot <your-domain>
#   4. Create ~/.cloudflared/config.yml pointing to localhost:8444
#   5. Change the tunnel command below to:
#        cloudflared tunnel run curator-bot
#      (remove the URL extraction + re-registration block — URL is stable)
#   See: TELEGRAM_WEBHOOK_PLAN.md > "Mac Mini Migration"
# ───────────────────────────────────────────────────────────────────────────────

set -e

PROJECT_DIR="$HOME/Projects/personal-ai-agents"
LOG_DIR="$PROJECT_DIR/logs"
WEBHOOK_PORT=8444

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR" || exit 1

export TELEGRAM_CHAT_ID="8379221702"
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"

source venv/bin/activate

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🤖 Telegram Webhook Stack"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Step 1: Start webhook server ───────────────────────────────────────────────
echo "▶ Starting webhook server on port $WEBHOOK_PORT..."
python telegram_bot.py --webhook >> "$LOG_DIR/telegram_bot.log" 2>&1 &
WEBHOOK_PID=$!
echo "  Server PID: $WEBHOOK_PID"

# Wait for Flask to start
sleep 2

if ! kill -0 $WEBHOOK_PID 2>/dev/null; then
    echo "❌ Webhook server failed to start. Check $LOG_DIR/telegram_bot.log"
    exit 1
fi

# Verify server is accepting connections
if curl -sf "http://localhost:$WEBHOOK_PORT/health" > /dev/null 2>&1; then
    echo "  ✅ Server healthy"
else
    echo "  ⚠️  Server may still be starting up"
fi

# ── Step 2: Start temporary Cloudflare tunnel ─────────────────────────────────
echo "▶ Starting Cloudflare tunnel..."
TUNNEL_LOG="$LOG_DIR/cloudflared.log"

cloudflared tunnel --url "http://localhost:$WEBHOOK_PORT" \
    --logfile "$TUNNEL_LOG" \
    --no-autoupdate \
    2>> "$LOG_DIR/cloudflared_err.log" &
TUNNEL_PID=$!
echo "  Tunnel PID: $TUNNEL_PID"

# Wait for trycloudflare.com URL to appear in the log
TUNNEL_URL=""
echo "  Waiting for tunnel URL"
for i in $(seq 1 30); do
    sleep 1
    printf "."
    TUNNEL_URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | head -1)
    if [ -n "$TUNNEL_URL" ]; then
        echo ""
        break
    fi
done

if [ -z "$TUNNEL_URL" ]; then
    echo ""
    echo "❌ Could not get tunnel URL after 30s."
    echo "   Check: $TUNNEL_LOG"
    echo "   Check: $LOG_DIR/cloudflared_err.log"
    kill $WEBHOOK_PID $TUNNEL_PID 2>/dev/null
    exit 1
fi

echo "  ✅ Tunnel URL: $TUNNEL_URL"

# ── Step 3: Register webhook with Telegram ────────────────────────────────────
WEBHOOK_FULL_URL="${TUNNEL_URL}/webhook"
echo "▶ Registering webhook: $WEBHOOK_FULL_URL"

REGISTER_RESULT=$(python3 - <<PYEOF
import keyring, requests, sys, secrets as _secrets

token = keyring.get_password('telegram', 'bot_token')
if not token:
    print('ERROR: No bot token in keyring')
    sys.exit(1)

# Get existing secret or generate a new one
secret = keyring.get_password('telegram', 'webhook_secret')
if not secret:
    secret = _secrets.token_hex(32)
    keyring.set_password('telegram', 'webhook_secret', secret)
    print('Generated new webhook secret')
else:
    print('Using existing webhook secret')

resp = requests.post(
    f'https://api.telegram.org/bot{token}/setWebhook',
    json={
        'url': '${WEBHOOK_FULL_URL}',
        'allowed_updates': ['callback_query', 'message'],
        'secret_token': secret,
        'drop_pending_updates': True,
    },
    timeout=10
)
data = resp.json()
if data.get('ok'):
    print('REGISTERED')
else:
    print(f'ERROR: {data}')
    sys.exit(1)
PYEOF
)

if echo "$REGISTER_RESULT" | grep -q "REGISTERED"; then
    echo "  ✅ Webhook registered"
else
    echo "  ❌ Registration failed:"
    echo "  $REGISTER_RESULT"
    kill $WEBHOOK_PID $TUNNEL_PID 2>/dev/null
    exit 1
fi

# ── Status ─────────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Stack running"
echo "   Webhook server : PID $WEBHOOK_PID  (localhost:$WEBHOOK_PORT)"
echo "   Cloudflare tunnel: PID $TUNNEL_PID  ($TUNNEL_URL)"
echo "   Telegram webhook : $WEBHOOK_FULL_URL"
echo ""
echo "   Logs:"
echo "     Bot : $LOG_DIR/telegram_bot.log"
echo "     Tunnel: $TUNNEL_LOG"
echo ""
echo "   Press Ctrl+C to stop and deregister."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Cleanup on exit ────────────────────────────────────────────────────────────
cleanup() {
    echo ""
    echo "▶ Shutting down..."

    # Deregister webhook so polling-based tools work again
    python3 - <<PYEOF
import keyring, requests
token = keyring.get_password('telegram', 'bot_token')
if token:
    r = requests.post(f'https://api.telegram.org/bot{token}/deleteWebhook',
                      json={'drop_pending_updates': False}, timeout=5)
    print(f"  Webhook deregistered: {r.json().get('ok')}")
PYEOF

    kill $WEBHOOK_PID $TUNNEL_PID 2>/dev/null
    echo "  ✅ Stopped"
}

trap cleanup EXIT INT TERM

# Keep alive — wait for tunnel process
wait $TUNNEL_PID
