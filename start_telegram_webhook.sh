#!/bin/bash
# start_telegram_webhook.sh
# Starts the full webhook stack: webhook server + ngrok tunnel + Telegram registration
#
# ─── TUNNEL MODE ───────────────────────────────────────────────────────────────
# CURRENT:    ngrok static domain (free tier)
#             - Stable URL — never changes between restarts
#             - No re-registration needed after first run
#             - Auth: ngrok config add-authtoken <token>  (one-time setup)
#             - Domain: nonconstricted-endodermal-karin.ngrok-free.dev
#
# Mac Mini migration: install ngrok, run `ngrok config add-authtoken <token>`
#   Same script, zero code changes.
# ───────────────────────────────────────────────────────────────────────────────

set -e

PROJECT_DIR="$HOME/Projects/personal-ai-agents"
LOG_DIR="$PROJECT_DIR/logs"
WEBHOOK_PORT=8444
TUNNEL_URL="https://nonconstricted-endodermal-karin.ngrok-free.dev"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR" || exit 1

export TELEGRAM_CHAT_ID="8379221702"
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"

source venv/bin/activate

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🤖 Telegram Webhook Stack"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Pre-flight: clear any orphaned processes ──────────────────────────────────
if lsof -ti:$WEBHOOK_PORT > /dev/null 2>&1; then
    echo "⚠️  Port $WEBHOOK_PORT in use — killing orphaned process..."
    lsof -ti:$WEBHOOK_PORT | xargs kill -9 2>/dev/null
    sleep 1
fi
if pgrep -f ngrok > /dev/null 2>&1; then
    echo "⚠️  ngrok already running — killing orphaned tunnel..."
    pkill -f ngrok 2>/dev/null
    sleep 1
fi

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

if curl -sf "http://localhost:$WEBHOOK_PORT/health" > /dev/null 2>&1; then
    echo "  ✅ Server healthy"
else
    echo "  ⚠️  Server may still be starting up"
fi

# ── Step 2: Start ngrok tunnel ────────────────────────────────────────────────
echo "▶ Starting ngrok tunnel ($TUNNEL_URL)..."
TUNNEL_LOG="$LOG_DIR/ngrok.log"

ngrok http "$WEBHOOK_PORT" \
    --domain="nonconstricted-endodermal-karin.ngrok-free.dev" \
    --log=stdout \
    --log-format=json \
    >> "$TUNNEL_LOG" 2>&1 &
TUNNEL_PID=$!
echo "  Tunnel PID: $TUNNEL_PID"

# Wait for ngrok to establish
sleep 4

if ! kill -0 $TUNNEL_PID 2>/dev/null; then
    echo "❌ ngrok failed to start. Check $TUNNEL_LOG"
    kill $WEBHOOK_PID 2>/dev/null
    exit 1
fi

echo "  ✅ Tunnel running: $TUNNEL_URL"

# ── Step 3: Register webhook with Telegram ────────────────────────────────────
WEBHOOK_FULL_URL="${TUNNEL_URL}/webhook"
echo "▶ Registering webhook: $WEBHOOK_FULL_URL"

REGISTER_RESULT=$(python3 - <<PYEOF
import keyring, requests, sys, secrets as _secrets

token = keyring.get_password('telegram', 'bot_token')
if not token:
    print('ERROR: No bot token in keyring')
    sys.exit(1)

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
echo "   Webhook server: PID $WEBHOOK_PID  (localhost:$WEBHOOK_PORT)"
echo "   ngrok tunnel  : PID $TUNNEL_PID  ($TUNNEL_URL)"
echo "   Telegram webhook: $WEBHOOK_FULL_URL"
echo ""
echo "   Logs:"
echo "     Bot  : $LOG_DIR/telegram_bot.log"
echo "     ngrok: $TUNNEL_LOG"
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
