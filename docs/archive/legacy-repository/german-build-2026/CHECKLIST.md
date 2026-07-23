# Language German — Build Checklist & Test Plan
**Spec:** `_NewDomains/language-german/SPEC.md` (v1.1)  
**Plan:** `_NewDomains/language-german/PLAN.md`  
**Build approach:** Incremental — confirm each step before proceeding to the next.

---

## Before Any Code — Environmental Checks

| Check | Command | Pass condition |
|-------|---------|----------------|
| Telegram bridge | Send test transcript to OpenClaw bot | OpenClaw receives, saves file, no error |
| `anthropic` package | `python3 -c "import anthropic; print(anthropic.__version__)"` | Prints a version number |

**Do not proceed past here until both pass.**

---

## Step 1 — Scaffold (folders + config files, no Python)

**What gets built:**
- Full folder tree under `_NewDomains/language-german/language/german/`
- `config/domain.json` — active persona, lesson counter, model name
- `config/personas.json` — all 8 Vienna personas with their scenarios
- `README.md` — one-paragraph domain overview
- `.gitignore` additions for sessions/, anki/, lessons/, progress.json

**Test:**
```
ls _NewDomains/language-german/language/german/config/
→ domain.json  personas.json

python3 -c "import json; print(json.load(open('config/domain.json'))['active_persona'])"
→ Frau Berger

python3 -c "import json; d=json.load(open('config/personas.json')); print(len(d), 'personas')"
→ 8 personas
```
✅ Done when: both JSON files parse cleanly, 8 personas present.

---

## Step 1.5 — Test Fixture

**What gets built:**
- `test_fixtures/sample_transcript.txt` — realistic raw Grok output for a bakery session

Content includes the `GERMAN_SESSION_TRANSCRIPT` header, Date/Persona/Scenario fields, and 7 alternating turns with a deliberate gender error (`der Brot` → `das Brot`) for the reviewer to catch.

**Test:**
```
cat test_fixtures/sample_transcript.txt | head -5
→ GERMAN_SESSION_TRANSCRIPT
→ Date: 2026-04-20
→ Persona: Frau Berger
→ Scenario: bakery_order
```
✅ Done when: file exists and header is readable. OpenClaw adjusts content if Grok's actual output format differs.

---

## Step 2 — `parse_transcript.py`

**What gets built:**
- Reads the raw transcript text (file or stdin)
- Detects `GERMAN_SESSION_TRANSCRIPT` trigger keyword
- Parses header fields (Date, Persona, Scenario)
- Parses alternating speaker turns into `raw_transcript` array
- Generates collision-safe session ID (`YYYY-MM-DD_NNN`)
- Writes `sessions/YYYY-MM-DD_001.json`

**Test:**
```bash
cd _NewDomains/language-german
python3 parse_transcript.py \
  --input test_fixtures/sample_transcript.txt \
  --base-dir language/german/

ls language/german/sessions/
→ 2026-04-20_001.json

python3 -c "
import json
s = json.load(open('language/german/sessions/2026-04-20_001.json'))
print('session_id:', s['session_id'])
print('persona:', s['persona'])
print('turns:', len(s['raw_transcript']))
print('reviewer_output:', s['reviewer_output'])
"
→ session_id: 2026-04-20_001
→ persona: Frau Berger
→ turns: 7
→ reviewer_output: None
```
✅ Done when: session JSON exists, fields are correct, `reviewer_output` is null.

**Run it twice** to confirm session ID increments to `_002` without overwriting `_001`.

---

## Step 3 — `reviewer.py`

**What gets built:**
- Reads session JSON
- Calls Claude Sonnet 4.6 with transcript + system prompt
- Parses response (3-stage: direct JSON → code block extract → fallback struct)
- Populates `reviewer_output` in session JSON
- Calls Anki CSV generator → writes `anki/YYYY-MM-DD_anki.csv`
- Calls lesson plan generator → writes `lessons/YYYY-MM-DD_lesson.json`
- Calls `_update_progress()` → writes/updates `progress.json`
- Sets `anki_generated: true`, `next_lesson_generated: true` in session JSON

**Test:**
```bash
python3 reviewer.py \
  --session language/german/sessions/2026-04-20_001.json \
  --base-dir language/german/

# Check 1: reviewer_output populated
python3 -c "
import json
s = json.load(open('language/german/sessions/2026-04-20_001.json'))
r = s['reviewer_output']
print('summary:', r['overall_summary'][:60])
print('errors found:', len(r['errors']))
print('anki_generated:', s['anki_generated'])
"

# Check 2: Anki CSV exists and has rows
cat language/german/anki/2026-04-20_anki.csv

# Check 3: lesson plan exists
python3 -c "
import json
l = json.load(open('language/german/lessons/2026-04-20_lesson.json'))
print('next persona:', l['persona'])
print('scenario:', l['scenario'])
"

# Check 4: progress.json updated
python3 -c "
import json
p = json.load(open('language/german/progress.json'))
print('total_sessions:', p['total_sessions'])
print('anki_cards:', p['anki_cards_generated'])
print('vocab_seen:', p['vocabulary_seen'])
"
```
✅ Done when: all four checks pass. Gender error (`der Brot`) should appear in `errors` list.

---

## Step 4 — `status.py`

**What gets built:**
- Reads `progress.json`, most recent session JSON, most recent lesson JSON
- Prints formatted terminal summary (no external deps)

**Test:**
```bash
python3 status.py --base-dir language/german/
```

Expected output shape:
```
── German Practice Status ─────────────────────────────
Sessions: 1 | Minutes: 12 | Anki cards: N

Last session (2026-04-20 · Frau Berger — bakery_order):
  Overall: [summary from reviewer]
  Errors: gender×1, ...
  Next focus: [next_focus from reviewer]

Tomorrow's lesson (Lesson 2):
  Persona: [next persona]
  Scenario: [next scenario]
  ...

Top error pattern: gender (N total)
───────────────────────────────────────────────────────
```
✅ Done when: output renders without errors and all sections are populated with real data.

---

## End-to-End Smoke Test (after all steps pass)

Run the full pipeline from raw text to status in one sequence:

```bash
BASE=language/german

# 1. Parse
python3 parse_transcript.py --input test_fixtures/sample_transcript.txt --base-dir $BASE

# 2. Review
LATEST=$(ls $BASE/sessions/*.json | sort | tail -1)
python3 reviewer.py --session $LATEST --base-dir $BASE

# 3. Status
python3 status.py --base-dir $BASE
```

✅ Done when: all three commands run without error and status output is coherent.

---

## Commit After Each Step

Each step gets its own commit before proceeding:
- Step 1: `feat(german): scaffold folder structure and config files`
- Step 1.5: `feat(german): add sample transcript test fixture`
- Step 2: `feat(german): parse_transcript.py — raw text to session JSON`
- Step 3: `feat(german): reviewer.py — LLM review, anki, lesson plan, progress`
- Step 4: `feat(german): status.py — terminal readout`
