# German Practice — User Guide
**Version:** 1.0  
**Date:** 2026-04-21  
**Author:** Robert van Stedum  
**Status:** Living document — update as workflow evolves

> This guide describes the daily German practice workflow end to end. It is the reference for Robert, OpenClaw, and any agent working on the language-german domain. When the workflow changes, update this document first.

---

## Overview

Daily German practice has three phases:

1. **Get today's session** — ask OpenClaw, receive a combined prompt package
2. **Practice** — paste into Grok on iPhone, have the conversation
3. **Process** — paste transcript to pipeline bot, receive review and Anki cards

Everything else is optional or automated. These three steps are the daily minimum.

---

## Phase 1 — Get Today's Session

### On any device (laptop or iPhone via Telegram)

Send to `minimoi_agent_bot`:

```
Pull today's German session
```

Or any natural variation:
- "What's my German session today?"
- "Give me today's German prompt"
- "German session please"

### What OpenClaw sends back

A single combined message in this format:

```
📚 Today's German Session — Lesson N
Persona: [Name] — [Role]
Scenario: [scenario_label]
Carry forward: [error or vocabulary from last session]

─── PASTE THIS INTO GROK ───

[full persona prompt — copy everything in this section]

─── HOW TO END THE SESSION ───

When finished, say to Grok:
"End session. Give me a clean transcript in this format:

---SESSION---
Date: YYYY-MM-DD
Persona: [Persona Name]
Scenario: [scenario_label]
Duration: [your estimate in minutes]

[Persona Name]: [turn text]
Robert: [turn text]
[continue alternating turns...]
---END---"

Then copy everything between ---SESSION--- and ---END---
and paste to minimoi_cmd_bot.
```

### How OpenClaw finds this information

- Today's lesson plan: `_NewDomains/language-german/language/german/lessons/` — most recent file
- Persona prompt: `_NewDomains/language-german/language/german/config/prompts/[persona_name].txt`
- If no lesson plan exists yet: use `domain.json → active_persona` and `bakery_order` as default scenario

---

## Phase 2 — Practice on iPhone

1. Open Grok iOS → start a new chat
2. Paste the full prompt from the `─── PASTE THIS INTO GROK ───` section
3. Say: **"Start today's session"**
4. Practice for 10–15 minutes — speak naturally, make mistakes, recover
5. When finished, say the end phrase exactly:

```
End session. Give me a clean transcript in this format:

---SESSION---
Date: YYYY-MM-DD
Persona: [Persona Name]
Scenario: [scenario_label]
Duration: [your estimate in minutes]

[Persona Name]: [turn text]
Robert: [turn text]
[continue alternating turns...]
---END---
```

6. Grok produces the formatted transcript
7. Copy everything from `---SESSION---` to `---END---` inclusive

**Tips:**
- Replace YYYY-MM-DD with today's date before saying the end phrase, or let Grok fill it in
- Duration is your rough estimate — 8, 10, 12 minutes — doesn't need to be exact
- If Grok doesn't format it correctly, say: "Please reformat using exactly the template I gave you"

---

## Phase 3 — Process the Transcript

Send to **`minimoi_cmd_bot`** (the pipeline bot):

Paste the transcript block directly — everything from `---SESSION---` to `---END---`.

### What happens automatically

1. `telegram_bot.py` detects the `---SESSION---` delimiter
2. Saves raw transcript to `sessions/inbox/`
3. Runs `parse_transcript.py` → creates session JSON
4. Runs `reviewer.py` → calls Claude Sonnet, generates feedback
5. Generates Anki CSV → `anki/YYYY-MM-DD_anki.csv`
6. Generates lesson plan → `lessons/YYYY-MM-DD_lesson.json`
7. Updates `progress.json`
8. Sends structured summary back to you on Telegram

### What the summary looks like

```
✅ Session reviewed — Herr Fischer / hotel_checkin
Errors: word_order×2, gender×1
New Anki cards: 5
Tomorrow: Maria — café_order (Lesson 3)
Next focus: V2 word order with time adverbs

Rate this session: 1️⃣ 2️⃣ 3️⃣ 4️⃣ 5️⃣
```

Tap a rating — this is your feedback signal. It takes one second and matters for long-term progress tracking.

---

## Anki Cards

After each session, a CSV file is generated at:
`_NewDomains/language-german/language/german/anki/YYYY-MM-DD_anki.csv`

**To import into Anki desktop:**
1. Open Anki
2. File → Import
3. Navigate to the anki/ folder
4. Select today's CSV
5. Map fields: Front, Back, Tags
6. Import

Cards include example sentences and tags for filtering by topic (food, hotel, directions, etc.).

---

## Telegram Bots — Which Bot Does What

| Bot | Use for |
|---|---|
| `minimoi_agent_bot` | Talk to OpenClaw — pull today's session, ask questions, get status |
| `minimoi_cmd_bot` | Pipeline execution — paste transcripts, `!german` commands |

**Never send a transcript to `minimoi_agent_bot`** — OpenClaw will respond conversationally but nothing will be written to disk.

**Never ask OpenClaw questions on `minimoi_cmd_bot`** — it only understands pipeline commands.

---

## `!german` Commands (send to `minimoi_cmd_bot`)

| Command | What it does |
|---|---|
| `!german status` | Today's session summary + next lesson |
| `!german progress` | Cumulative stats — sessions, minutes, cards, top errors |
| `!german today` | Re-run reviewer on today's most recent session |
| `!german persona [name]` | Override tomorrow's persona |
| `!german anki` | Path to today's Anki CSV |
| `!german debug` | Dump last inbox file + last session header |

---

## The 8 Vienna Personas

| Persona | Role | Difficulty | Best for |
|---|---|---|---|
| Frau Berger | Bakery owner | Beginner | Daily warmup, informal Austrian |
| Herr Fischer | Hotel receptionist | Beginner | Formal Hochdeutsch, travel logistics |
| Maria | Café waitress | Intermediate | Natural pace, Viennese café vocabulary |
| Dr. Huber | Museum guide | Intermediate | Longer explanations, follow-up questions |
| Stefan | U-Bahn stranger | Intermediate | Directions, navigation, normal speed |
| Frau Novak | Pharmacist | Beginner | Practical vocabulary, precise language |
| Klaus | Upscale waiter | Advanced | Formal register, full meal interaction |
| Anna | Airbnb host | Beginner | Friendly, practical, apartment vocabulary |

Persona prompts live at:
`_NewDomains/language-german/language/german/config/prompts/`

---

## Daily Minimum (3 steps, 20 minutes total)

```
1. minimoi_agent_bot → "Pull today's German session"     [1 min]
2. Grok → paste prompt → practice → get transcript       [15 min]
3. minimoi_cmd_bot → paste transcript → rate session     [2 min]
```

That's it. Everything else — Anki review, HTML page, writing exercise — is optional and additive.

---

## What's Not Built Yet (Coming)

| Feature | Status | When |
|---|---|---|
| Auto-trigger (no delimiter needed) | Step 1 in build plan | This week |
| `!german start` persona menu | Step 2 in build plan | This week |
| Session quality rating buttons | Step 3 in build plan | This week |
| HTML session review page | Step 4 in build plan | This week |
| iCloud prompt file drop | Step 5 in build plan | This week |
| Morning German reminder | Planned | This week |

---

## Troubleshooting

**OpenClaw doesn't know today's lesson:**
- Check that yesterday's session was processed: `!german status` on `minimoi_cmd_bot`
- If no lesson exists, ask OpenClaw to use Frau Berger / bakery_order as default

**Pipeline didn't fire after pasting transcript:**
- Confirm you sent to `minimoi_cmd_bot`, not `minimoi_agent_bot`
- Confirm the transcript starts with `---SESSION---` on the first line
- Send `!german debug` to see what the last inbox file contains

**Grok didn't format the transcript correctly:**
- Say: "Please reformat using exactly the template I gave you, starting with ---SESSION--- and ending with ---END---"

**Reviewer output looks wrong:**
- Send `!german debug` to see the raw session JSON
- If `reviewer_raw_output` is populated, the LLM response failed to parse — check the raw output for clues

---

## File Locations (for reference)

```
_NewDomains/language-german/
  language/german/
    config/
      domain.json          ← active persona, lesson counter, reviewer model
      personas.json        ← all 8 personas with speaking prompts
      prompts/             ← one .txt file per persona (paste into Grok)
    sessions/              ← session JSONs (gitignored)
      inbox/               ← raw transcripts drop here first
    anki/                  ← daily Anki CSVs (gitignored)
    lessons/               ← daily lesson plans (gitignored)
    progress.json          ← cumulative stats (gitignored)
  parse_transcript.py
  reviewer.py
  status.py
```

---

## Document Changelog

| Version | Date | Changes |
|---|---|---|
| 1.0 | 2026-04-21 | Initial guide — covers current manual workflow and target automated workflow |
