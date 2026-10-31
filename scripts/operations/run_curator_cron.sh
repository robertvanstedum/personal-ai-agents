#!/bin/bash
# Curator dev-validation cron — runs the SAME docker container path as EC2
# production (docker exec into minimoi-curator), not the native Mac venv,
# so this actually exercises the environment where the keyring-import crash
# (issue #35 investigation, 2026-08-07) lived. The old native-venv version of
# this script had real Keychain access and never hit that bug at all — it
# only validated the idempotency-check fix, not the crash itself.
# Model: xAI grok-4.3 (--model=grok-4.3), typically ~$0.07-$0.09/day;
# fallback to mechanical scoring if the API is unavailable.
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

LOG_PREFIX="[curator-dev-cron]"
# ── Today's state: delivery ledger first, then generation (#305) ─────────────
# Generation and delivery are tracked separately. curator_rss_v2.py writes
# briefing_date into curator_latest.json when it generates; telegram_bot.py
# --send writes data/curator/telegram_delivery/<UTC date>.json. The ledger rules
# live in core/telegram/delivery_ledger.py, shared with the sender, so only an
# exactly valid record for today proves delivery. Prints one of:
#   delivered   today's record is a valid "acknowledged"   -> nothing to do
#   resolved    an operator resolved a held day as delivered or skipped
#                                                         -> nothing to do
#   hold        started/partial/uncertain, or any malformed or wrong-day record
#                                                         -> never resend; inspect
#   generated   generated today, not delivered            -> deliver only (no paid rescoring)
#   none        not generated today                       -> generate, then deliver
TODAY=$(date -u +%Y-%m-%d)
STATE=$(docker exec minimoi-curator python3 -c "from core.telegram.delivery_ledger import cron_state; print(cron_state())" 2>/dev/null)
if [ $? -ne 0 ]; then
  echo "$LOG_PREFIX ERROR: briefing state lookup failed — not running"
  exit 1
fi

case "$STATE" in
  delivered)
    echo "$LOG_PREFIX Briefing already delivered today ($TODAY) — skipping"
    exit 0 ;;
  resolved)
    echo "$LOG_PREFIX Today's held delivery ($TODAY) was resolved by an operator — nothing to send"
    exit 0 ;;
  hold)
    echo "$LOG_PREFIX HOLD: today's delivery ($TODAY) is started, partial, uncertain or its record is malformed — not resending. Inspect with: python3 -m core.telegram.delivery_hold status $TODAY"
    exit 1 ;;
  generated)
    echo "$LOG_PREFIX Briefing generated today but not delivered — retrying delivery only" ;;
  none)
    echo "$LOG_PREFIX Starting briefing at $(date -u)"

    # ── Pull new X bookmarks ──────────────────────────────────────────────────
    echo "$LOG_PREFIX Pulling X bookmarks..."
    python -m scripts.x.x_pull_incremental 2>&1 || \
      echo "$LOG_PREFIX scripts.x.x_pull_incremental failed — continuing with existing signals"

    # ── Run curator pipeline ──────────────────────────────────────────────────
    echo "$LOG_PREFIX Running RSS curation (grok-4.3)..."
    docker exec -e MINIMOI_ROLE=production minimoi-curator python domains/curator/curator_rss_v2.py \
      --model=grok-4.3 --fallback --temperature=0.7
    RSS_STATUS=$?

    # Fail closed: a failed curation never reaches Telegram. Nothing marks the
    # day generated, so the next hourly run in the window retries generation.
    if [ $RSS_STATUS -ne 0 ]; then
      echo "$LOG_PREFIX ERROR: curator_rss_v2.py exited with status $RSS_STATUS — not sending Telegram; the next hourly run in the window retries"
      exit 1
    fi ;;
  *)
    echo "$LOG_PREFIX ERROR: could not read today's briefing state — not running"
    exit 1 ;;
esac

# ── Deliver (strict; telegram_bot.py writes the delivery ledger) ─────────────
echo "$LOG_PREFIX Sending Telegram briefing (system bot)..."
docker exec -e MINIMOI_ROLE=production -e TELEGRAM_CHAT_ID=8379221702 minimoi-curator python core/telegram/telegram_bot.py --send
STATUS=$?

# STATUS is telegram_bot.py --send's exit code: 0 acknowledged, 1 definitely not
# sent (the next run retries delivery), 4 another delivery holds today's lock,
# anything else held for inspection.
case $STATUS in
  0) echo "$LOG_PREFIX Briefing complete at $(date -u)" ;;
  1) echo "$LOG_PREFIX ERROR: briefing not delivered (telegram_bot.py --send exited 1) — the next hourly run retries delivery"
     exit 1 ;;
  4) echo "$LOG_PREFIX BUSY: another delivery for $TODAY holds the lock — this run sent nothing"
     exit 1 ;;
  *) echo "$LOG_PREFIX HOLD: telegram_bot.py --send exited $STATUS — delivery uncertain or partial; not resending. Inspect with: python3 -m core.telegram.delivery_hold status $TODAY"
     exit 1 ;;
esac
