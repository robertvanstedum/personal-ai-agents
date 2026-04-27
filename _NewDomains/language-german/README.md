# language-german

German language practice pipeline. Runs as a domain inside the personal-ai-agents repo.

**Status:** Operational as of 2026-04-26. 27 sessions completed in 7 days. 8 Vienna personas. Voice and writing modes tracked separately.

---

## What it does

1. Generates a daily session prompt (persona + scenario + universal AI behavioral rules)
2. Delivers the prompt to Telegram and Dropbox
3. Watches the Dropbox inbox for transcripts dropped from iPhone
4. Parses transcripts → error review → Anki cards → next lesson plan

Everything after step 2 is automatic once the watcher is running.

---

## Daily workflow (3 steps)

```
1. @minimoi_cmd_bot ← !german session      [prompt arrives in Telegram + Dropbox]
2. Grok iOS         ← paste prompt, practice, drop transcript in Dropbox inbox
3. Pipeline fires automatically within 15 seconds
```

---

## Commands (`@minimoi_cmd_bot`)

| Command | Effect |
|---|---|
| `!german session` | Generate today's session prompt |
| `!german writing` | Generate writing-mode prompt (`Mode: writing` in footer) |
| `!german drill [N]` | Generate N drill prompts, first sent to Telegram |
| `!german status` | Progress summary — sessions, minutes, Anki cards, next lesson |
| `!german anki` | Path to today's Anki CSV |
| `!german watcher start` | Start Dropbox transcript watcher |
| `!german watcher stop` | Stop watcher |

**Direct mode:** `telegram_bot.py` handles all commands directly. OpenClaw is not needed for session commands. See `ORCHESTRATOR.md` for routing logic.

---

## Key files

```
_NewDomains/language-german/
  ORCHESTRATOR.md              ← agent-agnostic command routing reference
  GERMAN_USER_GUIDE.md         ← full workflow guide for daily use
  run_tests.py                 ← 9-test acceptance suite
  get_german_session.py        ← session package generator (--dropbox --send --writing --drill N)
  watch_transcripts.py         ← Dropbox inbox watcher (poll every 15s)
  parse_transcript.py          ← transcript parser (multi-line, em-dash, and Grok inline format)
  reviewer.py                  ← session reviewer + lesson rotation + Anki generation
  status.py                    ← progress summary
  import_cards.py              ← Anki auto-import via AnkiConnect
  test_fixtures/               ← transcript fixtures for acceptance tests
  language/german/
    config/
      domain.json              ← active persona, lesson counter
      personas.json            ← 8 personas with scenarios and warm-up variants
      prompts/                 ← one .txt file per persona (≤3500 chars)
      sync_config.json         ← Dropbox paths, watcher settings, agent_mode
    lessons/                   ← daily lesson plans (gitignored)
    sessions/                  ← session JSONs (gitignored)
    anki/                      ← daily Anki CSVs (gitignored)
    progress.json              ← cumulative stats (gitignored)
```

---

## The 8 Vienna personas

| Persona | Role | Register |
|---|---|---|
| Frau Berger | Bakery owner | Informal Austrian |
| Herr Fischer | Hotel receptionist | Formal Hochdeutsch |
| Maria | Café waitress | Natural, Viennese pace |
| Dr. Huber | Museum guide | Explanatory, follow-up questions |
| Stefan | U-Bahn stranger | Normal speed, directions |
| Frau Novak | Pharmacist | Precise, practical |
| Klaus | Upscale waiter | Formal register, phone reservations |
| Anna | Airbnb host | Friendly, apartment vocabulary |

Persona files: `language/german/config/prompts/`. Authoring standard: ≤3500 chars, 6-8 vocab items, 3-4 sentence description. Standard comment required on line 1.

---

## Universal session header

Every prompt includes a `=== SESSION INSTRUCTIONS ===` block before the persona text. This block instructs any AI model (Grok, Claude, ChatGPT) to:

- Play the correct gender
- Respect the scenario medium (phone call vs. in-person)
- Not prefix each turn with their name
- Respond in German only
- Gently correct errors in-character
- Wait for "Start today's session" before beginning

---

## Transcript format

Grok produces transcripts in `---SESSION---` / `---END---` format. The parser handles:
- Standard multi-line format
- Em-dash format (`—SESSION—`) from iPhone autocorrect
- Single-line format (`Date: 2026-04-26 Persona: Klaus Scenario: ...`) from Grok

---

## Running tests

```bash
cd _NewDomains/language-german && source ../../venv/bin/activate
python run_tests.py        # 9/9 expected
```

Tests cover: transcript parsing (all 3 formats), session output (header, footer, carry-forward, writing mode), persona file standard, ORCHESTRATOR.md completeness.

---

## Configuration

`language/german/config/sync_config.json`:

```json
{
  "agent_mode": "direct",
  "base_path": "~/Dropbox/German_Sessions",
  "poll_interval_seconds": 15,
  "max_prompt_chars": 4000
}
```

`agent_mode: "direct"` — `telegram_bot.py` handles session commands itself.
`agent_mode: "openclaw"` — session commands are routed to `@minimoi_agent_bot` instead.
