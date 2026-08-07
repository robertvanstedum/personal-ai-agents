#!/bin/bash
# Curator dev-validation cron — runs the SAME docker container path as EC2
# production (docker exec into minimoi-curator), not the native Mac venv,
# so this actually exercises the environment where the keyring-import crash
# (issue #35 investigation, 2026-08-07) lived. The old native-venv version of
# this script had real Keychain access and never hit that bug at all — it
# only validated the idempotency-check fix, not the crash itself.
# Model: xAI grok-4.3 (--model=grok-4.3) ~$0.30/day — fallback to mechanical if API down
# Runs hourly via the existing launchd job (com.vanstedum.curator, StartInterval=3600).
#
# On/off toggle (default OFF):
#   touch scripts/.curator_dev_cron_on   # enable
#   rm scripts/.curator_dev_cron_on      # disable
# This script always no-ops harmlessly when the local `minimoi-curator`
# container isn't running.

PROJECT_DIR="$HOME/Projects/personal-ai-agents"
cd "$PROJECT_DIR" || exit 1

if [ ! -f "$PROJECT_DIR/scripts/.curator_dev_cron_on" ]; then
    echo "⏸  Dev cron disabled (scripts/.curator_dev_cron_on not present) — skipping"
    exit 0
fi

# ── Time gate: once a day only, ~5:50PM CDT (22:50 UTC) ──────────────────────
# Narrow 2-hour window (not the wider retry window production uses) — this is
# a dev validation run, not a production job that needs hourly retry-on-fail.
# launchd's StartInterval=3600 doesn't land on exact clock hours, so the
# window has to be wide enough to reliably catch one tick per day, not
# exactly one hour.
HOUR=$(date -u +%H)
if [ "$HOUR" -lt 22 ]; then
    echo "⏭  Outside the once-daily 22:00–23:59 UTC window (hour=$HOUR UTC) — skipping"
    exit 0
fi

# ── Idempotency: skip if briefing already ran today (fixed check — #35) ──────
TODAY=$(date -u +%Y-%m-%d)
FILE_DATE=$(docker exec minimoi-curator python3 -c "
import json
try:
    d = json.load(open('data/curator/curator_latest.json'))
    print(d[0].get('briefing_date', '')[:10])
except Exception:
    print('')
" 2>/dev/null || true)

if [ "$FILE_DATE" = "$TODAY" ]; then
    echo "✅ Briefing already ran today ($TODAY) — skipping"
    exit 0
fi

echo "🚀 Starting curator briefing (docker) at $(date -u)"

# Phase 3C.7: Pull new X bookmarks before curating.
# Failure is isolated — log and continue, never block the briefing.
echo "🔖 Pulling new X bookmarks..."
python -m scripts.x.x_pull_incremental 2>&1 || echo "⚠️  scripts.x.x_pull_incremental failed — continuing with existing signals"

echo "🔖 Running RSS curation (grok-4.3)..."
docker exec -e MINIMOI_ROLE=production minimoi-curator python domains/curator/curator_rss_v2.py \
    --model=grok-4.3 --fallback --temperature=0.7

echo "📤 Sending Telegram briefing (system bot)..."
docker exec -e MINIMOI_ROLE=production -e TELEGRAM_CHAT_ID=8379221702 minimoi-curator python core/telegram/telegram_bot.py --send

STATUS=$?

if [ $STATUS -eq 0 ]; then
    docker exec minimoi-curator python3 -c "
import json, datetime
with open('data/curator/curator_latest.json') as f:
    data = json.load(f)
data[0]['briefing_date'] = datetime.date.today().isoformat()
with open('data/curator/curator_latest.json', 'w') as f:
    json.dump(data, f)
" 2>/dev/null || true
    echo "✅ Curator briefing generated and sent successfully at $(date -u)"
    exit 0
else
    echo "❌ ERROR: telegram_bot.py --send exited with status $STATUS"
    exit 1
fi
