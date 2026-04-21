# Mini-moi — Initial Build Plan
## Language German Domain + Platform Foundation
**Version:** 1.0  
**Date:** 2026-04-20  
**Status:** Active — Vienna trip May 2026  
**Author:** Robert van Stedum

> This is a short, concrete plan for the next steps. It respects the one-step-at-a-time principle. Nothing here gets built until the previous step is confirmed working. Subsequent phases are named but not overspecified — they will be detailed when the time comes.

---

## Where We Are Now

The language-german domain pipeline is built and validated:
- `parse_transcript.py` ✅
- `reviewer.py` ✅ — 4 errors found on real session data
- `status.py` ✅ — reviewed session filter working
- 8 Vienna personas + 35 speaking prompts ✅
- Telegram bridge working (manual paste) ✅
- Two-bot separation confirmed ✅ — `minimoi_cmd_bot` → pipeline, `minimoi_agent_bot` → OpenClaw
- All documents committed to GitHub ✅

**What's missing before Vienna is fully operational:**
1. Auto-trigger — transcript paste fires pipeline without manual step
2. `!german start` — persona menu via Telegram
3. Session quality rating — feedback signal closes the loop
4. HTML session review page — makes output human-readable
5. Prompt file drop to iCloud — daily persona prompt ready on iPhone

---

## Immediate Next Steps (This Week)

Each step requires explicit confirmation before the next begins.

### Step 1 — Auto-trigger in `telegram_bot.py`
**What:** Detect `---SESSION---` delimiter in incoming messages, fire pipeline automatically.  
**Why first:** Removes the biggest daily friction point. Every session after this is one paste, not a paste plus a manual trigger.  
**Done when:** Paste a transcript to `minimoi_cmd_bot` → pipeline fires → summary arrives on iPhone with no other action.  
**Owner:** Claude Code  
**Confirm before Step 2.**

### Step 2 — `!german start` persona menu
**What:** Send `!german start` → numbered menu of 8 personas → reply with number → full prompt returned.  
**Extension point:** `_get_prompt(persona_name)` function reads from `config/prompts/` — this is the hook for future automation.  
**Done when:** Full Herr Fischer prompt arrives in Telegram from a single `!german start` → `2` exchange.  
**Owner:** Claude Code  
**Confirm before Step 3.**

### Step 3 — Session quality rating
**What:** After each pipeline summary, `telegram_bot.py` sends inline keyboard: `Rate this session: 1️⃣ 2️⃣ 3️⃣ 4️⃣ 5️⃣`. Tap stores rating in session JSON.  
**Why now:** Six weeks of quality data before the intelligence layer needs it. Start the runway immediately.  
**Done when:** Tap a rating after a real session → session JSON contains `session_quality: N`.  
**Owner:** Claude Code  
**Confirm before Step 4.**

### Step 4 — HTML session review page
**What:** Flask route `/german/session/[date]` reads session JSON, renders transcript with errors highlighted, vocabulary, Anki cards in browser, tomorrow's lesson.  
**Why now:** Makes every session's output human-readable without opening JSON files. Closes the loop between pipeline output and daily use.  
**Done when:** Open browser after a session → full review visible, errors highlighted, cards readable.  
**Owner:** Claude Code  
**Confirm before Step 5.**

### Step 5 — Prompt file drop to iCloud
**What:** After `reviewer.py` generates the lesson plan, write a stamped prompt file to iCloud path: `/Users/vanstedum/Library/Mobile Documents/com~apple~CloudDocs/RVS 2026/RVS Agent Project/New Domains/prompt-texts/YYYY-MM-DD_lessonN_[persona]_[scenario].txt`  
**File is self-contained** — full persona prompt + today's speaking prompt + carry-forward errors. Copy entire file into Grok, say "Start today's session."  
**Done when:** After a real session, tomorrow's prompt file appears in iCloud, opens on iPhone, pastes cleanly into Grok.  
**Owner:** Claude Code  
**Confirm complete.**

---

## Vienna Readiness Checklist

Steps 1-3 must be complete before the Vienna trip. Steps 4-5 are high value but not blockers.

| Item | Required for Vienna | Status |
|---|---|---|
| Pipeline runs end to end | ✅ | Done |
| Transcript paste fires automatically | Yes | Step 1 |
| Persona prompt via Telegram | Yes | Step 2 |
| Session quality rating | Yes | Step 3 |
| HTML review page | No — nice to have | Step 4 |
| iCloud prompt file | No — nice to have | Step 5 |
| MacBook stays on at home | Decision needed | See below |

**MacBook Vienna decision (needed before May 1):**  
Options: (A) MacBook stays home plugged in — pipeline runs remotely. (B) Manual fallback — two commands, 30 seconds. Recommend A with B as backup. Mac Mini migration solves this permanently post-Vienna.

---

## After Vienna — Platform Foundation

These are named but not yet specified. They will be detailed after Vienna when real usage has revealed what actually matters.

**v1.2 priorities (Summer 2026):**
- Mac Mini migration — always-on, Tailscale, launchd services
- PostgreSQL activation — curator data migrated from flat files
- German progress dashboard — `/german/progress` Flask page
- Curator novelty scoring

**v1.3 priorities (Fall 2026):**
- `german_intelligence.py` — weekly analysis (needs 15-20 sessions of data first)
- Neo4j graph layer on top of Postgres
- First cross-domain connections

**These will be planned properly when v1.2 is complete.** No further specification now.

---

## One-Step-at-a-Time Reminder

Every step in this plan follows the same confirmation protocol:

1. Claude Code describes what it will build — Robert approves before any code is written
2. Claude Code builds one step only
3. Claude Code reports what was built and what was tested
4. Robert confirms it works
5. OpenClaw updates CHANGELOG
6. Claude Code commits and pushes
7. Robert says go for the next step

No exceptions. No bundling. No jumping ahead.
