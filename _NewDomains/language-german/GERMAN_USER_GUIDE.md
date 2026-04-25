# German Practice — User Guide
**Version:** 1.2  
**Date:** 2026-04-25  
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
Duration: [number only, e.g. 12]

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
Duration: [number only, e.g. 12]

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

## Writing Sessions

Writing sessions use the same personas, scenarios, and pipeline as voice sessions. Use them when Grok voice is unavailable, or to build a voice vs writing error comparison over time. The pipeline handles `Mode: writing` automatically — no other changes needed.

### Option A — Request via OpenClaw (when command is live)

Send to `minimoi_agent_bot`:

```
writing café Maria
writing hotel Herr Fischer
writing bakery Frau Berger
```

OpenClaw generates a writing-mode prompt file marked with `⌨️ WRITING SESSION` at the top and pre-fills `Mode: writing` in the transcript template. Everything else is the same as a normal session.

### Option B — Run manually today (before OpenClaw command is live)

1. Open **Claude.ai** or **Grok** in text mode (browser or app)
2. Paste the persona prompt from `config/prompts/[persona].txt`
3. Say: **"Start today's writing session"**
4. Type your turns — no voice required. The persona will gently correct mistakes in-character.
5. When finished, say:

```
End session. Give me a clean transcript in this format:

---SESSION---
Date: YYYY-MM-DD
Persona: [Persona Name]
Scenario: [scenario_label]
Duration: [number only, e.g. 12]
Mode: writing

[Persona Name]: [turn text]
Robert: [turn text]
[continue alternating turns...]
---END---
```

6. Copy the full block from `---SESSION---` to `---END---` inclusive
7. **Before submitting:** confirm `Mode: writing` is in the header

**Tip:** Turn off auto-correct before you start — the pipeline tracks your actual mistakes.

### iPhone workflow for writing sessions

1. Copy the persona prompt (from OpenClaw or the prompts folder)
2. Open Claude.ai or Grok in a browser tab or app
3. Paste the prompt → say "Start today's writing session"
4. Type your responses in full sentences
5. End with the transcript request above — include `Mode: writing` in the header
6. Copy the block → paste to `minimoi_cmd_bot` as normal

### Processing a writing transcript

Same as voice — paste to **`minimoi_cmd_bot`**. The pipeline detects `Mode: writing` and increments your writing session count separately from voice. Everything else (Anki cards, lesson plan, reviewer feedback) is identical.

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
| `!german session` | Generate today's session → send to Telegram + write to Dropbox |
| `!german drill [persona] [scenario] [N]` | Generate N drill prompts → all written to Dropbox, first sent to Telegram |
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
| Auto-trigger (no delimiter needed) | ✅ Freeform fallback shipped | Done |
| Dropbox bridge + watcher | ✅ Shipped 2026-04-25 | Done |
| Drill mode | ✅ Shipped 2026-04-25 | Done |
| Anki auto-import via AnkiConnect | ✅ Shipped 2026-04-25 | Done |
| Session quality rating buttons | Planned | Next |
| HTML session review page | Planned | Next |
| Morning German reminder | Planned | Next |

---

## Dropbox iPhone Workflow

The Dropbox bridge lets you go from Telegram command to Grok practice without copy-pasting between apps. The watcher on your Mac fires the pipeline within 15 seconds of a transcript landing in the inbox.

### Before practice

1. In Telegram (either bot), send: `!german session` or `"Pull today's German session"`
2. Pipeline generates today's prompt → sends to Telegram AND writes a `.txt` file to `~/Dropbox/German_Sessions/prompts/`
3. On your iPhone, open the Dropbox app → navigate to `German_Sessions/prompts/`
4. Open today's file → if Grok accepts `.txt` attachments, attach it directly; otherwise copy the text
5. In Grok: paste (or attach) → say **"Start today's session"** → practice

### After practice

1. Say to Grok: **"End session. Give me a clean transcript."**
2. Copy the full block from `---SESSION---` to `---END---` (inclusive)
3. In the Dropbox app: navigate to `German_Sessions/transcripts/inbox/` → create a new `.txt` file → paste → save
4. Done — the watcher detects the file within 15 seconds and fires the full pipeline

### What the watcher does automatically

1. Parses the transcript → creates session JSON
2. Runs `reviewer.py` → error analysis + Anki cards
3. Imports cards into Anki (if AnkiConnect is open on your Mac)
4. Generates the next session prompt → writes to Dropbox + sends to Telegram
5. Moves the transcript to `transcripts/processed/`

**Fallback:** If the Dropbox inbox doesn't work, paste the transcript directly to `minimoi_cmd_bot` as before — the manual path is always available.

---

## Rapid-Drill Mode

Use drill mode to repeat a scenario multiple times in quick succession — useful for cementing a specific skill (e.g. U-Bahn directions, ordering coffee) before Vienna.

### How to trigger

In Telegram:
```
!german drill Stefan ubahn 3
!german drill Maria café 5
!german drill Frau Novak pharmacy 2
```

Or natural language:
- "Start German session in drill mode"
- "Drill café Maria 3 times"
- "3 drill sessions"

### What happens

1. Pipeline generates N prompt files → all written to Dropbox (`prompts/..._drill1.txt` through `..._drillN.txt`)
2. The first session prompt is sent to Telegram immediately
3. Each drill file includes a `Drill-Session: K of N` line in the end-session transcript template — **you don't need to type this manually**

### Working through drills

- Open each drill file from Dropbox in order, practice, drop transcript in inbox
- After each non-final session: Anki cards are generated, but the lesson plan does **not** rotate — you stay on the same persona/scenario
- After the final session (`K == N`): full pipeline runs, lesson rotates to the next one

**Why drills don't advance the lesson:** Each mid-drill session generates Anki cards from your errors. The lesson rotation waits until you've done all N repetitions, so you're reinforcing the same material without disrupting the lesson sequence.

---

## Anki Auto-Import

After each session, a CSV file is written to `language/german/anki/YYYY-MM-DD_anki.csv`.

### Automatic import (when Anki is open)

The watcher calls `import_cards.py` automatically. If Anki desktop is open and AnkiConnect is running, cards are imported immediately. If not, the pipeline continues and logs a warning — nothing breaks.

**To enable AnkiConnect:** Install the AnkiConnect add-on in Anki (add-on code: `2055492159`). Leave Anki open on your Mac before dropping a transcript in the inbox.

### Manual import

1. Open Anki → File → Import
2. Navigate to `_NewDomains/language-german/language/german/anki/`
3. Select the most recent CSV → map fields: Front, Back, Tags → Import

### What the cards contain

- Front: German word or phrase (or fill-in-the-blank sentence)
- Back: English translation + example sentence
- Tags: session date, persona, error type (e.g. `word_order`, `gender`, `vocab`)

Use Anki's tag browser to filter by error type and drill your weakest areas.

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
      sync_config.json     ← Dropbox paths + watcher settings
    sessions/              ← session JSONs (gitignored)
    anki/                  ← daily Anki CSVs (gitignored)
    lessons/               ← daily lesson plans (gitignored)
    progress.json          ← cumulative stats (gitignored)
  parse_transcript.py
  reviewer.py
  watch_transcripts.py     ← Dropbox inbox watcher (run at startup)
  get_german_session.py    ← session package generator
  import_cards.py          ← Anki auto-import via AnkiConnect
  status.py

~/Dropbox/German_Sessions/
  prompts/                 ← generated session prompt files (.txt)
  transcripts/
    inbox/                 ← drop your transcript here after practice
    processed/             ← watcher moves files here after pipeline runs
  logs/
    watcher.log            ← watcher activity log (rotates at 1MB)
```

---

## Document Changelog

| Version | Date | Changes |
|---|---|---|
| 1.2 | 2026-04-25 | Add Dropbox iPhone workflow, Rapid-Drill mode, Anki auto-import sections; update commands table + What's Not Built Yet |
| 1.1 | 2026-04-24 | Add Writing Sessions section — OpenClaw command, manual workflow, iPhone steps, pipeline note |
| 1.0 | 2026-04-21 | Initial guide — covers current manual workflow and target automated workflow |
