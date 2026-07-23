# SPEC: File Sync Bridge — German Practice Domain
**Version:** 1.3 (Final — ready for Claude Code)
**Author:** Robert van Stedum + Grok (v1.1) + Claude (v1.2, v1.3) + OpenClaw (feasibility)
**Status:** Approved — commit to GitHub, implement this weekend
**Parent spec:** `_NewDomains/language-german/SPEC.md`
**Date:** 2026-04-24

---

## 1. Purpose

Two-way file bridge between MacBook (OpenClaw + pipeline) and iPhone (Grok/Claude voice
sessions) using Dropbox as transport. A polling watcher on the Mac replaces manual Telegram
copy-paste for both prompt delivery and transcript submission. Supports single sessions and
rapid-drill mode (N sessions on the same scenario back-to-back).

---

## 2. Design Principles

- **Dropbox-first, fully swappable.** One JSON config owns all paths and provider.
  Changing provider (iCloud, Syncthing, OneDrive, local network share) = edit one value.
- **Filesystem polling, no SDK.** Watcher uses stdlib only (`os.listdir` + `time.sleep`).
  No Dropbox SDK, no API token, no new pip installs. Dropbox desktop app handles sync
  transparently — watcher sees a normal local folder.
- **Full history preserved.** Every prompt and transcript timestamped and kept forever
  for future database loading.
- **Rapid-drill friendly.** 10 café sessions in a row, with consolidated drill summary.
- **Telegram fallback always operational.** Both paths work in parallel. No existing
  behavior is removed.

---

## 3. Folder Structure

```
~/Dropbox/German_Sessions/
├── prompts/
│   ├── 2026-04-24_1430_lesson8_maria_cafe.txt
│   └── 2026-04-24_1445_lesson8_maria_cafe_drill2.txt
├── transcripts/
│   ├── inbox/          ← drop transcripts here from iPhone
│   └── processed/      ← watcher moves here after success (history kept)
└── logs/
    └── watcher.log
```

**Filenames include full timestamp (YYYY-MM-DD_HHMM_)** for collision-free multi-session
days and clean database loading later.

All folders created automatically by watcher on first run if they don't exist.

---

## 4. Config File

**Path:** `_NewDomains/language-german/language/german/config/sync_config.json`

```json
{
  "sync_provider": "dropbox_local",
  "base_path": "~/Dropbox/German_Sessions",
  "prompts_dir": "prompts",
  "transcripts_inbox_dir": "transcripts/inbox",
  "transcripts_processed_dir": "transcripts/processed",
  "watcher_mode": "poll",
  "poll_interval_seconds": 15,
  "supported_extensions": [".txt"],
  "pipeline_base_dir": "_NewDomains/language-german/language/german",
  "send_telegram_on_start": true,
  "send_telegram_on_success": true,
  "send_telegram_on_error": true,
  "keep_history_days": 9999
}
```

**To swap provider later:** change `sync_provider` and `base_path`. Nothing else changes.

---

## 5. Watcher — `watch_transcripts.py`

**Path:** `_NewDomains/language-german/watch_transcripts.py`
**Dependencies:** stdlib only — `os`, `time`, `shutil`, `subprocess`, `pathlib`

**Behavior:**

1. On startup: create folder structure if missing, send Telegram confirmation:
   `"🟢 German watcher active — polling ~/Dropbox/German_Sessions every 15s"`
2. Poll `transcripts/inbox/` every 15 seconds using `os.listdir`
3. On new `.txt` file detected:
   - Wait 2 seconds (allow Dropbox sync to complete write)
   - Run `parse_transcript.py --stdin --base-dir [pipeline_base_dir]`
   - Run `reviewer.py --latest --base-dir [pipeline_base_dir]`
   - If session JSON has `drill_mode: true` AND not the final drill session:
     update error counts + generate Anki cards only — skip lesson plan rotation
   - If normal session OR final drill session:
     run full lesson plan generation with next persona
   - Move file from `inbox/` to `processed/` (history preserved)
   - Send Telegram success summary
4. On any failure: send Telegram error message with filename and error text.
   Do not move file to processed — leaves it in inbox for manual retry.
5. Logs all activity to `logs/watcher.log`. Rotate at 1MB.

**Start:**
```bash
cd ~/Projects/personal-ai-agents/_NewDomains/language-german
~/Projects/personal-ai-agents/venv/bin/python3 watch_transcripts.py
```

**Stop:**
```bash
pkill -f watch_transcripts
```

**Check running:**
```bash
pgrep -fl watch_transcripts
```

Manual start is the default. launchd plist for auto-start is optional and kept local
(not committed to git), following the same pattern as the OpenClaw plist.

---

## 6. Transcript Format Contract

The full dialogue must be present inside the session block. Header-only blocks will
fail parse. The `Duration` field must be a plain number — no "minutes" suffix.

```
---SESSION---
Date: 2026-04-24
Persona: Maria
Scenario: cafe_order
Duration: 12

Maria: Servus! Was darf's sein?
Robert: Ich möchte einen Kaffee, bitte.
Maria: Einen Kleinen Braunen oder eine Melange?
Robert: Eine Melange, bitte.
[continue alternating turns...]
---END---
```

---

## 7. Prompt File Format

Each prompt file written to `prompts/` by OpenClaw:

```
📚 Lesson 8 — Maria / Café Order
Date: 2026-04-24
Carry-forward: einen (not ein) with masculine nouns
Warm-up: Order a Verlängerter + Kipferl, then ask about the Tageskarte

─── PASTE INTO GROK OR CLAUDE ───

[full persona prompt text from config/prompts/maria_cafe.txt]

─── HOW TO END THE SESSION ───

When finished say: "End session. Give me a clean transcript."

Grok/Claude will output the full session block.
Copy everything from ---SESSION--- to ---END--- inclusive.

Open Dropbox app on iPhone:
  German_Sessions → transcripts → inbox → create new .txt file → paste → save

Pipeline fires within 15 seconds.
```

---

## 8. Prompt Delivery — OpenClaw Integration

After every `reviewer.py` run (normal or drill), OpenClaw:

1. Reads the generated lesson plan from `lessons/YYYY-MM-DD_lesson.json`
2. Reads the persona prompt from `config/prompts/[persona].txt`
3. Assembles the combined prompt file (lesson header + warm-up + full persona prompt +
   end-session instructions)
4. Writes timestamped file to `~/Dropbox/German_Sessions/prompts/`

Filename format: `YYYY-MM-DD_HHMM_lessonN_persona_scenario.txt`
For drill sessions: `YYYY-MM-DD_HHMM_lessonN_persona_scenario_drillK.txt`

---

## 9. Rapid-Drill Mode

**Purpose:** Run N sessions on the same scenario back-to-back to build automatic fluency.
Example: 10 café sessions in a row to make ordering feel natural before Vienna.

**OpenClaw command syntax:**
```
drill café Maria 10
drill hotel Herr Fischer 5
drill ubahn Stefan 3
```

(Syntax confirmed by OpenClaw feasibility review.)

**What OpenClaw does:**
1. Generates N prompt files, each timestamped 2 minutes apart
2. Each file marked with `drill_mode: true`, `drill_session: K`, `drill_total: N`
3. Warm-up cycles through `warm_up_variants` array from `personas.json` (see section 10)
4. Writes all N files to `prompts/` at once — Robert works through them at his own pace

**Pipeline behavior during drill:**
- Each session: parse + review + update error counts + generate Anki (deduplicated)
- Sessions 1 through N-1: no persona rotation, no new lesson plan
- Session N (final): full lesson plan generation with next persona

**Drill summary — sent by OpenClaw after final session processes:**
```
🔁 Drill complete — 10x Maria / café_order
Sessions: 10 | Total errors: 28
Best session: #7 (1 error)
Most improved: masculine accusative (8 errors → 1 error)
New Anki cards: 14 (deduplicated across all sessions)
Next lesson: Stefan — U-Bahn (Lesson 9)
```

---

## 10. `personas.json` Addition

Add `warm_up_variants` array to each persona (3–4 entries). Used by rapid-drill to
cycle warm-ups across sessions so they don't feel repetitive.

Example for Maria:
```json
"warm_up_variants": [
  "Order a Melange and a Kipferl.",
  "Order a Verlängerter and ask for the Tageskarte.",
  "Ask if the Gugelhupf is fresh, then order a Kleiner Brauner.",
  "Ask for a table for two, then order drinks and ask for the bill."
]
```

All 8 personas need this field added.

---

## 11. iPhone Workflow (Updated)

**Before practice:**
1. Dropbox app → German_Sessions → prompts
2. Open today's timestamped prompt file → select all → copy
3. Paste into Grok or Claude.ai → say "Start today's session"

**After practice:**
1. Say: "End session. Give me a clean transcript."
2. Copy full block from `---SESSION---` to `---END---` inclusive
3. Dropbox app → German_Sessions → transcripts → inbox
4. Create new `.txt` file → paste → save
5. Done — pipeline fires within 15 seconds

---

## 12. Build Sequence for Claude Code

Execute in order. Do not skip steps.

1. Create `sync_config.json` at
   `_NewDomains/language-german/language/german/config/sync_config.json`
2. Build `watch_transcripts.py` at `_NewDomains/language-german/watch_transcripts.py`
   — polling, stdlib only, creates folder structure on first run
3. Add `warm_up_variants` array to all 8 personas in `personas.json`
4. Update `reviewer.py` to respect `drill_mode` flag — skip lesson rotation on
   non-final drill sessions, generate Anki cards for all sessions
5. Update OpenClaw: write timestamped prompt files after each lesson/review cycle;
   handle `drill [scenario] [persona] [N]` command
6. Update `GERMAN_USER_GUIDE.md` — add Dropbox workflow section + rapid-drill
   instructions; Telegram workflow section unchanged
7. Test sequence:
   - Drop 3 manual transcripts in inbox → confirm pipeline fires and files move
   - Run `drill café Maria 3` → confirm 3 prompt files generated, drill summary sent

**Telegram workflow untouched throughout. Both paths operational after build.**

---

## 13. Cloud Portability — Roadmap Item (not this sprint)

The pipeline is a stateless worker pattern: files in → Python scripts → files out →
Telegram. It runs identically on a VPS or Mac Mini with only `base_path` changed in
`sync_config.json`.

**Roadmap entry (add to BACKLOG.md):**
> B-XXX: Validate German domain + full pipeline runs on VPS or Mac Mini with no code
> changes — config path update only. Target: after 3+ weeks of prototype use.
> Cloud VPS ($6/month Hetzner) is fallback if Mac Mini migration delays past Vienna.

Do not spec further until prototype has proven stable across multiple weeks.

---

## 14. Open Questions (resolved)

| Question | Resolution |
|---|---|
| iCloud path sandboxing | Use `~/Dropbox` local path instead — fully accessible |
| Watcher: poll vs watchdog | Poll — stdlib only, no lock-in |
| Drill syntax | `drill café Maria 10` — confirmed by OpenClaw |
| Watcher start: auto vs manual | Manual for v1 — launchd plist optional, local only |
| Dropbox SDK needed? | No — Dropbox desktop app handles sync, watcher sees local filesystem |

---

*Document history: Grok v1.1 → Claude v1.2 (feasibility fixes) → OpenClaw (path fix) →
Claude v1.3 (final). Ready for Claude Code plan mode review and commit.*
