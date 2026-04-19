# Vienna Build Plan — Language German Domain
**Status:** Active  
**Trip date:** May 10, 2026  
**Time remaining:** ~3 weeks  
**Built:** 2026-04-19  
**Purpose:** Concrete, time-bound action plan to get the German practice pipeline fully operational before Vienna. Companion to SPEC.md, PLAN.md, and CHECKLIST.md.

---

## What "Done Before Vienna" Means

The pipeline is usable from Vienna if and only if:

1. Robert can finish a Grok Voice session on iPhone
2. Paste the transcript into `minimoi_cmd_bot` or `@rvsopenbot` on Telegram
3. Receive a review summary, Anki cards, and tomorrow's lesson plan back on iPhone
4. Without touching the MacBook

Everything else is a nice-to-have.

---

## Current State (as of 2026-04-19)

| Component | Status | Notes |
|---|---|---|
| `parse_transcript.py` | ✅ Built and tested | Handles header, speaker turns, collision-safe IDs |
| `reviewer.py` | ✅ Built and tested | 4 errors found on real session, Anki CSV with tags |
| `status.py` | ✅ Built and tested | Filters to reviewed sessions, graceful empty state |
| Persona library | ✅ 8 personas, 35 speaking prompts | All Vienna travel scenarios covered |
| Telegram bridge | ✅ Working (manual paste) | `minimoi_cmd_bot` receives, pipeline fires, summary returned |
| Auto-trigger | 🔲 Not yet wired | Requires keyword hook in OpenClaw or telegram_bot.py |
| Morning reminder | 🔲 Not built | Daily German prompt from OpenClaw |
| Anki import | 🔲 Not validated | Cards generated, import into Anki desktop not yet confirmed |
| MacBook always-on | 🔲 Decision needed | Vienna workaround required — see below |

---

## Remaining Build Items (Priority Order)

### 1. Auto-trigger (High — do this week)
**What:** When Robert pastes a transcript to Telegram, the pipeline fires automatically without manual intervention in OpenClaw webchat.  
**Current state:** Manual relay — paste to Telegram, then tell OpenClaw to run the pipeline in webchat.  
**Fix:** Wire `GERMAN_SESSION_TRANSCRIPT` keyword detection directly in `telegram_bot.py` message handler. Claude Code owns this.  
**Done when:** Paste transcript to Telegram → summary arrives on iPhone with no other action required.

### 2. Morning German Reminder (Medium — do this week)
**What:** Daily scheduled message from OpenClaw in German reminding Robert to do his session.  
**Example:** *"Guten Morgen! Frau Berger wartet auf dich. 🥐 Zeit für deine tägliche Übung."*  
**Timing:** 7am daily, or whatever time Robert sets.  
**Done when:** Message arrives on iPhone at the right time without Robert doing anything.

### 3. Anki Import Validation (Medium — do this week)
**What:** Confirm the generated CSV imports cleanly into Anki desktop and cards display correctly.  
**Current state:** Cards are generating with Front/Back/Tags format. Import not yet tested.  
**Done when:** Cards from a real session appear in Anki with correct fields and tags.

### 4. MacBook Vienna Workaround (High — decide before May 1)
**The constraint:** The pipeline runs on the MacBook. If the MacBook travels to Vienna, it needs to stay on and connected for the Telegram bridge to work. If it stays home, Robert has no pipeline.

**Options:**
- **A) MacBook stays home, lid open, plugged in.** Remote access via existing setup. Pipeline runs. Risk: home network outage, power outage.
- **B) MacBook travels to Vienna.** Pipeline works wherever MacBook is. Risk: luggage, battery, hotel WiFi.
- **C) Accept manual fallback for Vienna.** Run the pipeline manually after each session — two commands, ~30 seconds. No always-on requirement.

**Recommendation:** Option A for Vienna. MacBook stays home, lid open. If it goes down, Option C covers it. The Mac Mini migration (v1.2) solves this permanently post-Vienna.

**Decision needed from Robert before May 1.**

---

## Vienna Daily Workflow (Target State)

**On iPhone (10–15 min, morning):**
1. Open Grok → German Practice chat
2. Say: *"Start today's session"*
3. Practice with assigned persona (see tomorrow's lesson in last Telegram summary)
4. Say: *"End session. Give me a clean transcript."*
5. Prepend four header lines, paste into `minimoi_cmd_bot`
6. Receive summary on iPhone within ~30 seconds

**Header template to keep in iPhone Notes:**
```
GERMAN_SESSION_TRANSCRIPT
Date: YYYY-MM-DD
Persona: [name]
Scenario: [scenario]
Duration: [minutes]

[paste transcript here]
```

**On iPhone (2 min, same morning or later):**
- Open Anki on iPhone, sync, review new cards

That's the full daily loop. No MacBook required if always-on is solved.

---

## Post-Vienna Graduation Checklist

After returning from Vienna, evaluate against these criteria before graduating to main repo:

- [ ] Telegram bridge was stable across 5+ real sessions
- [ ] Reviewer output quality was genuinely useful (caught real errors, not noise)
- [ ] Anki cards were imported and reviewed regularly
- [ ] Progress tracking showed meaningful error patterns over time
- [ ] Daily routine felt sustainable — not burdensome

If all pass: graduate to `language/german/` in main repo, evaluate `language_core/` shared library.  
If any fail: archive with post-mortem, apply learnings to next language domain attempt.

---

## What This Build Proves

Beyond German practice, this domain validates three things for mini-moi:

1. **The pipeline pattern generalizes.** Ingest → process → deliver → feedback works for language learning, not just news curation.
2. **Mobile-first is achievable.** A voice session on iPhone can produce structured review output, Anki cards, and a lesson plan with no desktop interaction.
3. **The extensibility claim is real.** A new domain was built in one day, integrated into the existing stack, and validated on real data. The `_NewDomains/` graduation lifecycle works.
