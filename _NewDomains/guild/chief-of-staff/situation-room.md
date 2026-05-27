# Situation Room — Escalation Protocol
**Purpose:** When something breaks or demands urgent attention, the Situation Room
defines the path from detection to resolution.

---

## Escalation Levels

| Level | Condition | First action |
|-------|-----------|-------------|
| **P1 — Down** | Daily briefing not delivered by 8 AM | Check Curator launchd log immediately |
| **P2 — Degraded** | Briefing delivered but obviously malformed | Review curator_latest.html + log |
| **P3 — Warning** | API cost spike, Grok errors > 5%, scoring anomaly | Monitor for 1 day before escalating |
| **Info** | New idea, opportunity, or observation | Log in intent-register open questions |

---

## P1 Response: Daily Briefing Failure

1. Check launchd status: `launchctl list | grep curator`
2. Check log: `cat ~/Projects/personal-ai-agents/logs/curator_daily.log | tail -50`
3. Run manually: `cd ~/Projects/personal-ai-agents && python curator_rss_v2.py`
4. If API failure: check xAI key validity and cost limits
5. Telegram bot offline: `launchctl list | grep telegram`

---

## P1 Response: German Domain Down

1. Check Flask process: `curl http://localhost:8767/health` (or whichever port)
2. Check launchd: `launchctl list | grep german`
3. Restart: `launchctl kickstart -k gui/$(id -u)/com.vanstedum.german-server`

---

## Incident Log

| Date | Domain | Issue | Resolution | Root cause |
|------|--------|-------|------------|-----------|
| (fill in) | | | | |
