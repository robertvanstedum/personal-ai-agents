#!/bin/bash
# Curator cron job for EC2 — mirrors run_curator_cron.sh but runs via docker exec.
# Scheduled hourly via crontab (see setup_ec2_cron.sh). Idempotent — skips if
# briefing already ran today or node is not production.

LOG_PREFIX="[curator-cron]"

# ── Role guard: exit silently if this node is not production ──────────────────
ROLE=$(docker exec minimoi-curator python -c \
  "from utils.role import role_label; print(role_label())" 2>/dev/null)
if [ "$ROLE" != "production" ]; then
  echo "$LOG_PREFIX Standby node (MINIMOI_ROLE=$ROLE) — skipping"
  exit 0
fi

# ── Time gate: only run between 12:00 and 20:00 UTC (7AM–3PM CDT) ────────────
HOUR=$(date -u +%H)
if [ "$HOUR" -lt 12 ] || [ "$HOUR" -ge 20 ]; then
  echo "$LOG_PREFIX Outside 12:00–20:00 UTC window (hour=$HOUR UTC) — skipping"
  exit 0
fi

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
    docker exec minimoi-curator python -m scripts.x.x_pull_incremental 2>&1 || \
      echo "$LOG_PREFIX scripts.x.x_pull_incremental failed — continuing with existing signals"

    # ── Run curator pipeline ──────────────────────────────────────────────────
    echo "$LOG_PREFIX Running RSS curation (grok-4.3)..."
    docker exec minimoi-curator python domains/curator/curator_rss_v2.py \
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
docker exec -e TELEGRAM_CHAT_ID=8379221702 minimoi-curator python core/telegram/telegram_bot.py --send
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
