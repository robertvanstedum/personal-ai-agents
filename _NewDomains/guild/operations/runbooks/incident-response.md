# Incident Response Runbook

Quick-reference playbook for the most common production failures.
Detailed escalation protocol: [situation-room.md](../../chief-of-staff/situation-room.md)

---

## Curator Daily Briefing Not Delivered

**Symptom:** No Telegram message by 8 AM.

```bash
# 1. Check if launchd ran
launchctl list | grep curator

# 2. Check the log
cat ~/Projects/personal-ai-agents/logs/curator_daily.log | tail -80

# 3. Run manually (dry run first)
cd ~/Projects/personal-ai-agents
python curator_rss_v2.py --dry-run

# 4. Run for real if dry run looks good
python curator_rss_v2.py
```

Common causes:
- xAI API key expired or rate limit hit
- Network connectivity failure during RSS fetch
- launchd not running (Mac Mini slept despite settings)
- Python environment issue after OS update

---

## Telegram Bot Not Responding

**Symptom:** Callback buttons (Like/Dislike/Save) not working, or `/run` command ignored.

```bash
# Check if bot is running
launchctl list | grep telegram

# Restart if needed
launchctl kickstart -k gui/$(id -u)/com.vanstedum.minimoi-cmd-bot

# Check bot log
cat ~/Library/Logs/minimoi-cmd-bot.log | tail -40
```

---

## German Flask Server Unreachable

**Symptom:** German domain URL returns connection refused.

```bash
# Check launchd status (fill in correct label after Mac Mini migration)
launchctl list | grep german

# Check what port it should be on (see MACMINI_MIGRATION_PLAN_v2.md)
# Restart:
# launchctl kickstart -k gui/$(id -u)/com.vanstedum.german-server
```

---

## Grok API Cost Spike

**Symptom:** Daily cost >> $0.30 (baseline).

Check: `cat ~/Projects/personal-ai-agents/curator_costs.json | python3 -m json.tool | tail -20`

Causes to investigate:
- Unusually large candidate pool (RSS fetch returned many articles)
- Score retries due to parsing failures
- Temperature set high (should be 1.0, not higher)

---

*Add new runbooks as incidents are encountered.*
