# mini-moi — German Domain
## Complete Build Handoff
**Date:** 2026-05-01  
**Vienna departure:** May 10, 2026 — 9 days  
**Author:** Robert van Stedum + Claude.ai  
**Grok schema review:** ✅ Pass — no conflicts, additive only  
**Base spec:** SPEC_simplified_interface_v0.5.md (OpenClaw-reviewed)  
**Scaffold spec:** german_scaffold_spec_v1.0.docx (Grok-reviewed)  
**Status:** Ready for Claude Code plan mode review and build  

> This document is the single source of truth for the build. It supersedes all prior partial handoffs from 2026-05-01. Pass to Claude Code plan mode. Do not start coding until plan mode review is complete.

---

## Section 1 — Context & Constraints

The German language domain pipeline is operational. All additions are strictly additive. No build step breaks the existing workflow at any point.

**Non-negotiable compatibility rule:**
- `!german` commands continue to work throughout all steps
- `---SESSION---` formatted paste continues to work throughout
- Dropbox watcher continues to work as Path 3 fallback
- All 9 existing tests must pass after every step before proceeding
- `_strip_llm_fences()`, `_SESSION_RE`, `_DRILL_RE`, `_WRITING_RE` must remain intact

**Ways of working:**
- Claude Code owns all git operations — add, commit, push
- OpenClaw owns file system writes only — never runs git commands
- One step at a time — confirm tests pass before next step begins
- Robert is the decision point between all agents

---

## Section 2 — Changes Made 2026-05-01 (Confirmed Baseline)

| Item | Status | Detail |
|---|---|---|
| Voice message handler (Whisper/OpenAI) | ✅ Done | `handle_voice_polling` in `telegram_bot.py` — commit 1619c57 |
| NL triggers: 'german session', 'next session', 'next lesson' | ✅ Done | `_SESSION_RE` expanded at module level — commit 1619c57 |
| Fence-stripping fix `_strip_llm_fences()` | ✅ Done | `reviewer.py:126`, `max_tokens` raised to 4096 — commit ac3b715 |
| Writing session fix verified | ✅ Done | 9/9 tests pass — committed to main, worktree synced |
| `carry_forward_phrases` added to `progress.json` | ✅ Done | 5 manual phrases present — `_carry_forward()` does not yet read them (fixed in Step 2) |
| OpenAI API key in keychain | ✅ Done | Whisper transcription confirmed end-to-end |
| Fuzzy parser (all 5 formats) | ✅ Done | Inline splitting, mode normalization, header tolerance |

---

## Section 3 — Current State vs. Spec v0.5

### Phase 1 — Transcript Submission
| Item | Status | Notes |
|---|---|---|
| Fuzzy parser (all 5 formats) | ✅ Done | Pre-built today |
| `.txt` file upload via Telegram | ❌ Not built | Step 1 — `filters.Document.TXT` handler missing |

### Phase 2 — Natural Language Commands
| Item | Status | Notes |
|---|---|---|
| `keyword_map.json` config file | ❌ Not built | Created in Step 2 with full scaffold data |
| Intent detection reads from config | ❌ Not built | Step 3 — hardcoded regexes today |
| `'again'` / `'one more'` intent | ❌ Not built | Step 4 |
| Persona keyword routing (`'café session'` → Maria) | ❌ Not built | Step 3 |
| Two-message delivery (briefing + AI prompt split) | ❌ Not built | Step 2 |
| Unknown intent → helpful fallback | ❌ Not built | Step 3 |
| Intent safety (standalone word check) | ❌ Not built | Step 3 — atomic with keyword map |

### Phase 3 & 4 — Post-Vienna
| Item | Status | Notes |
|---|---|---|
| Flexible drill mode | ❌ Not built | Post-Vienna |
| Batch drill review | ❌ Not built | Post-Vienna |
| Continuation scenario | ❌ Not built | Post-Vienna explicitly |

---

## Section 4 — Confirmed Build Order

Locked 2026-05-01. Each step requires all 9 tests passing before next step begins.

| Step | Scope | Files | Gate |
|---|---|---|---|
| **1** | `.txt` file upload handler — `MessageHandler(filters.Document.TXT)`. Read content, 50KB size limit with error message, feed same `_handle_german_transcript` pipeline. | `telegram_bot.py` | 9 tests pass |
| **2** | Two-message delivery + `carry_forward_phrases` + scaffold block — Split `get_german_session.py` into Message 1 (briefing) and Message 2 (AI prompt). Add `carry_forward_phrases` read in `_carry_forward()`. Add scaffold block to Message 1. Create `keyword_map.json` with full phrase bank data (Section 6). 1-second delay between messages. | `get_german_session.py`, `_carry_forward()`, `keyword_map.json`, `progress.json` | 9 tests pass |
| **3** | NL intent detection + intent safety — Read `keyword_map.json` (already created in Step 2). Expand `handle_text_message` for persona keywords, `'again'` routing, unknown intent fallback. Standalone word safety check ships in this step — atomic, do not split. | `telegram_bot.py` | 9 tests pass |
| **4** | `'again'` intent — Repeat same persona/scenario without rotation. `repeat: true` flag in session JSON. Rotation suppressed after repeat. | `telegram_bot.py`, `reviewer.py`, `progress.json` | 9 tests pass |
| **5+** | Phase 3 drill mode, batch review, continuation — Post-Vienna. | TBD | Post-Vienna |

---

## Section 5 — Scaffold Block Spec (New — Ships in Step 2)

### Problem
Robert's receptive competence is ahead of productive competence. He understands sessions but stalls on output generation — particularly in café, hotel, and Airbnb scenes. The stall is at sentence assembly speed, not vocabulary knowledge.

### Solution
Each session's Message 1 (YOUR BRIEFING) includes a scaffold block of 3 phrases drawn from the active persona's phrase bank. Rotates across sessions so the full bank of 6 phrases surfaces within a week of practice with any persona.

### Scaffold Block Structure (per session)
- Phrase 1 — transaction sentence (core scene action)
- Phrase 2 — preference expression (forces active choice vocabulary)
- Phrase 3 — recovery phrase (always fixed per persona — what to say when frozen)

The scaffold block appears in **Message 1 ONLY**. Never in Message 2 (the AI prompt). The AI does not know what phrases Robert has been given — this tests spontaneous deployment.

### Message 1 Format Example
```
📋 YOUR BRIEFING — read this, do not paste into Grok

📚 Lesson 48 — Dr. Huber / Museum Navigation
Carry forward: Wo finde ich die ägyptische Sammlung?
Warm-up: Review vocabulary from last session
Goal: Ask for directions inside the museum, confirm you understood

🧱 Today's scaffold — try to use these:
   • Was empfehlen Sie uns zu sehen?
   • Wir möchten lieber selbst durch das Museum gehen.
   • 🆘 If you freeze: Entschuldigung — ich suche die richtigen Worte.
```

### Data Structure

| Field | Location | Type | Notes |
|---|---|---|---|
| `scaffold_phrases` | `keyword_map.json` per persona | `Array [{de, en, type}]` | 6 phrases per persona |
| `recovery_phrase` | `keyword_map.json` per persona | String | Always shown as Phrase 3 |
| `scaffold_rotation_index` | `progress.json` | `Dict {persona_name: int}` | Advances by 2 per session, resets at 6 |
| `scaffold_delivered` | session JSON | Array of strings | Phrases shown this session |
| `scaffold_used` | session JSON | Array of strings | Populated by reviewer after transcript analysis |
| `scaffold_avoided` | session JSON | Array of strings | Delivered but not used — high-priority Anki candidates |

### Rotation Logic
Bank has 6 phrases. Each session draws 2 (indices [n] transaction + [n+1] preference) plus fixed recovery. Index advances by 2. After index reaches 6, reset to 0. Rotation is per-persona — Maria's index never affects Dr. Huber's.

### Backward Compatibility — Mandatory Safety Checks (Grok-flagged)

These are mandatory. Implement before any scaffold logic:

1. **`get_german_session.py`** — use `persona.get('scaffold_phrases', [])` and `persona.get('recovery_phrase', None)` — skip scaffold block entirely if either is missing or empty. No crash.
2. **`reviewer.py`** — do not assume `scaffold_delivered`, `scaffold_used`, `scaffold_avoided` exist on old session JSONs — use `.get()` with empty list defaults throughout.
3. **`progress.json` write-back** — initialize `scaffold_rotation_index` as `{}` if key missing, then `.get(persona_name, 0)` for each persona lookup.

---

## Section 6 — Phrase Banks (All 8 Personas)

Use this data exactly as written when building `keyword_map.json` in Step 2. Do not paraphrase or modify phrases.

**Stall levels:** HIGH = café/hotel/Airbnb (Robert's confirmed stall scenes). MEDIUM = museum/directions/pharmacy. LOW = restaurant/bakery.

---

### Maria — Café Waitress
**Stall: HIGH** | Scenarios: cafe_order, cafe_bill, cafe_meal, cafe_small_talk

| # | German | English | Type |
|---|---|---|---|
| 1 | Ich hätte gerne einen kleinen Brauner, bitte. | I'd like a small Viennese coffee, please. | transaction |
| 2 | Was empfehlen Sie heute? | What do you recommend today? | preference |
| 3 | Wir möchten gerne bestellen. | We'd like to order. | transaction |
| 4 | Könnte ich bitte die Rechnung haben? | Could I have the bill please? | transaction |
| 5 | Ich möchte lieber einen Tee. | I'd prefer a tea. | preference |
| 6 | Meine Frau hätte gerne den Apfelstrudel. | My wife would like the apple strudel. | transaction |
| 🆘 | Einen Moment bitte — ich überlege kurz. | (recovery — always Phrase 3) | recovery |

---

### Herr Fischer — Hotel Receptionist
**Stall: HIGH** | Scenarios: hotel_checkin, hotel_checkout, hotel_complaint, directions

| # | German | English | Type |
|---|---|---|---|
| 1 | Ich habe eine Reservierung auf den Namen Van Stedum. | I have a reservation under the name Van Stedum. | transaction |
| 2 | Wann ist der Check-out? | When is check-out? | transaction |
| 3 | Ich möchte lieber ein ruhiges Zimmer. | I'd prefer a quiet room. | preference |
| 4 | Könnten Sie uns ein Taxi rufen? | Could you call us a taxi? | transaction |
| 5 | Gibt es WLAN im Zimmer? | Is there WiFi in the room? | transaction |
| 6 | Ich hätte gerne einen späteren Check-out, wenn möglich. | I'd like a later check-out if possible. | preference |
| 🆘 | Entschuldigung — könnten Sie das bitte wiederholen? | (recovery — always Phrase 3) | recovery |

---

### Anna — Airbnb Host
**Stall: HIGH** | Scenarios: apartment_handoff, apartment_questions, apartment_problem

| # | German | English | Type |
|---|---|---|---|
| 1 | Wo finde ich den Schlüssel? | Where do I find the key? | transaction |
| 2 | Wie funktioniert die Heizung? | How does the heating work? | transaction |
| 3 | Ich hätte gerne die WLAN-Daten. | I'd like the WiFi details. | transaction |
| 4 | Wir möchten lieber früh einchecken. | We'd prefer to check in early. | preference |
| 5 | Gibt es einen Schlüssel für das Fahrrad? | Is there a key for the bicycle? | transaction |
| 6 | Wo sind die nächsten Supermärkte? | Where are the nearest supermarkets? | transaction |
| 🆘 | Wie sagt man das auf Deutsch — einen Moment. | (recovery — always Phrase 3) | recovery |

---

### Dr. Huber — Museum Guide
**Stall: MEDIUM** | Scenarios: museum_navigation, museum_recommendation, museum_exhibit

| # | German | English | Type |
|---|---|---|---|
| 1 | Was empfehlen Sie uns zu sehen? | What do you recommend we see? | preference |
| 2 | Wie lange dauert die Führung? | How long does the tour last? | transaction |
| 3 | Ich hätte gerne zwei Eintrittskarten. | I'd like two tickets. | transaction |
| 4 | Wo ist die ägyptische Sammlung? | Where is the Egyptian collection? | transaction |
| 5 | Wir möchten lieber selbst durch das Museum gehen. | We'd prefer to walk through the museum ourselves. | preference |
| 6 | Was ist Ihr persönlicher Tipp? | What is your personal recommendation? | preference |
| 🆘 | Entschuldigung — ich suche die richtigen Worte. | (recovery — always Phrase 3) | recovery |

---

### Stefan — U-Bahn / Directions
**Stall: MEDIUM** | Scenarios: ubahn_directions, street_directions, transport

| # | German | English | Type |
|---|---|---|---|
| 1 | Wie komme ich zum Naschmarkt? | How do I get to the Naschmarkt? | transaction |
| 2 | Welche Linie fährt dorthin? | Which line goes there? | transaction |
| 3 | Wie viele Stationen sind es? | How many stops is it? | transaction |
| 4 | Ich möchte lieber zu Fuß gehen. | I'd prefer to walk. | preference |
| 5 | Wo kaufe ich eine Fahrkarte? | Where do I buy a ticket? | transaction |
| 6 | Ist es weit von hier? | Is it far from here? | transaction |
| 🆘 | Einen Moment — ich verstehe, aber ich suche das Wort. | (recovery — always Phrase 3) | recovery |

---

### Frau Novak — Pharmacist
**Stall: MEDIUM** | Scenarios: pharmacy_medicine, pharmacy_advice

| # | German | English | Type |
|---|---|---|---|
| 1 | Ich habe Kopfschmerzen. | I have a headache. | transaction |
| 2 | Haben Sie etwas gegen Husten? | Do you have something for a cough? | transaction |
| 3 | Ich hätte gerne etwas gegen Magenschmerzen. | I'd like something for a stomach ache. | transaction |
| 4 | Ich möchte lieber etwas ohne Rezept. | I'd prefer something without a prescription. | preference |
| 5 | Wie oft soll ich das nehmen? | How often should I take this? | transaction |
| 6 | Meine Frau ist allergisch gegen Penicillin. | My wife is allergic to penicillin. | transaction |
| 🆘 | Entschuldigung — wie sagt man das auf Deutsch? | (recovery — always Phrase 3) | recovery |

---

### Klaus — Restaurant
**Stall: LOW** | Scenarios: restaurant_reservation, restaurant_order, restaurant_complaint

| # | German | English | Type |
|---|---|---|---|
| 1 | Haben Sie einen Tisch für zwei Personen? | Do you have a table for two? | transaction |
| 2 | Ich hätte gerne das Tagesmenu. | I'd like the daily menu. | transaction |
| 3 | Was empfehlen Sie als Hauptspeise? | What do you recommend as a main course? | preference |
| 4 | Wir möchten lieber draußen sitzen. | We'd prefer to sit outside. | preference |
| 5 | Könnten Sie bitte die Speisekarte bringen? | Could you bring the menu please? | transaction |
| 6 | Die Rechnung, bitte — zusammen. | The bill please — together. | transaction |
| 🆘 | Entschuldigung — einen Moment bitte. | (recovery — always Phrase 3) | recovery |

---

### Frau Berger — Bakery
**Stall: LOW** | Scenarios: bakery_order, bakery_small_talk

| # | German | English | Type |
|---|---|---|---|
| 1 | Ich hätte gerne zwei Kipferl, bitte. | I'd like two croissants, please. | transaction |
| 2 | Was ist frisch heute? | What is fresh today? | preference |
| 3 | Ich möchte lieber das dunkle Brot. | I'd prefer the dark bread. | preference |
| 4 | Haben Sie Nussstrudel? | Do you have nut strudel? | transaction |
| 5 | Das wäre alles, danke. | That would be all, thank you. | transaction |
| 6 | Könnte ich bitte eine Tüte haben? | Could I have a bag please? | transaction |
| 🆘 | Einen Moment — ich überlege noch. | (recovery — always Phrase 3) | recovery |

---

## Section 7 — Key Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Intent safety — 'session' in unrelated text triggers pipeline | Standalone word check ships atomically with keyword map in Step 3. Never merge Step 3 without it. |
| Two-message ordering — Telegram doesn't guarantee order under load | 1-second delay between Message 1 and Message 2. Must not be omitted. |
| `keyword_map.json` missing at runtime | Fall back gracefully to `!german`-only mode. Not crash. Explicit fallback in code. |
| `scaffold_phrases` missing for a persona | `persona.get('scaffold_phrases', [])` — skip scaffold block silently. No crash. |
| Old session JSONs missing scaffold fields | `reviewer.py` uses `.get()` with empty list defaults throughout. |
| Whisper dependency on OpenAI key | Key confirmed in keychain. faster-whisper local A/B test planned post-Vienna. |
| `carry_forward_phrases` unused through Vienna | Fixed in Step 2 — `_carry_forward()` reads `progress.json carry_forward_phrases`. |

---

## Section 8 — Instructions for Claude Code

### Plan Mode Review — Do This First

Before writing any code, review this document in plan mode and confirm:

1. Current state of `telegram_bot.py`, `get_german_session.py`, `reviewer.py`, `progress.json`, and `keyword_map.json` matches Section 2 and Section 3
2. `keyword_map.json` does not already exist with a conflicting structure
3. No existing code already partially implements scaffold, two-message delivery, or `.txt` upload in a conflicting way
4. Grok's three safety checks (Section 5) are understood and will be implemented before scaffold logic

Report findings before starting Step 1. If anything conflicts with this document, flag it. Do not resolve conflicts silently.

### Build Rules

- Start with Step 1. Do not skip ahead.
- All 9 existing tests pass after every step before proceeding.
- Steps 3 keyword map and intent safety are atomic — commit together, never separately.
- `keyword_map.json` is created in Step 2 (with scaffold data). Step 3 reads from it — does not recreate it.
- Scaffold block in Message 1 only. Never in Message 2.
- Do not touch `_strip_llm_fences()`, `_SESSION_RE`, `_DRILL_RE`, `_WRITING_RE` in any step.
- Phrase banks in Section 6 are used exactly as written — no paraphrasing.

### Commit Message Convention

- Step 1: `feat(telegram): add .txt file upload handler — Phase 1 Step 2`
- Step 2: `feat(session): two-message delivery + scaffold block + carry_forward_phrases`
- Step 3: `feat(telegram): keyword_map NL intent + standalone word safety`
- Step 4: `feat(telegram): 'again' intent + repeat:true flag`

---

*mini-moi German Domain — Complete Build Handoff v1.0 — 2026-05-01 — Robert van Stedum + Claude.ai + Grok*
