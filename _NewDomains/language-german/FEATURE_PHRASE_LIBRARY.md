# Feature: German Phrase Library

**Status:** Shipped — 2026-05-12  
**Created:** 2026-05-12  
**Author:** Robert + Claude Code  
**Read by:** Claude Code (build) · OpenClaw (context)

---

## The Real-World Insight

This feature came directly from Robert being in Vienna.

The learning pattern that emerged naturally in the field:

> Ask a shopkeeper, barista, or bartender: **"Wie sagt man auf Deutsch, [English phrase]?"**  
> They say it back in German.  
> You repeat it with them several times, right there.  
> You've now learned the phrase — aurally, in context, with a real person.

The problem: **that phrase lives only in your head for the next 10 minutes.** You're in the middle of a conversation. You can't sit down and add it to Anki. You need to capture it *now*, before it fades.

Later — that evening, the next morning — you want to be able to pull it back out and confirm you can still spell it correctly. The oral learning already happened. The bot's job is spelling reinforcement, not teaching.

**This is not a drill feature. It is a capture-and-recall feature.**

---

## What We Are Building

A **phrase library** — a permanent store of German phrases learned in the wild. Separate from the verb drill system. Phrases live in the library until the user intentionally promotes them into a verb drill.

### Core concept

```
CAPTURE (mobile, instant)
   ↓
LIBRARY (phrasebook.json — permanent home)
   ↓
PRACTICE (spelling recall — user-initiated, lightweight)
   ↓
PROMOTE (optional — move phrase into formal verb drill)
```

The library is the master store. Drills are "checked out" items. Phrases can exist in the library indefinitely without ever becoming drills.

---

## User-Facing Commands

All commands use the `!phrase` prefix (separate from `!german`).

### Capture — `!phrase german | english`

Mobile, standing in a shop:

```
!phrase Nein danke, ich schaue nur. | No thanks, I'm just looking.
```

Bot replies:
```
✅ Saved (#001)
🇩🇪 Nein danke, ich schaue nur.
🇺🇸 No thanks, I'm just looking.
```

Zero friction. One line. Done.

---

### List — `!phrase list [N]`

```
!phrase list
```

Shows last 10 phrases, newest first:

```
#001 🇩🇪 Nein danke, ich schaue nur.
     🇺🇸 No thanks, I'm just looking. [library, 0 practices]
```

---

### Practice (spelling recall) — `!phrase practice [id]`

```
!phrase practice
```

Bot selects the most recently added, least practiced library phrase and sends:

```
You learned this one in the wild — type it from memory:
🇩🇪 Nein danke, ich schaue nur.
🇺🇸 No thanks, I'm just looking.

→ Type the German spelling:
```

User types their attempt. Bot checks spelling (fuzzy match, threshold 85%):

- Correct: `✅ Correct spelling.`
- Close: `Close — correct spelling:\n🇩🇪 Nein danke, ich schaue nur.`

Practice count increments either way. One round per `!phrase practice` call — user calls again when ready for more.

---

### Promote to drill — `!phrase drill [id]`

```
!phrase drill 001
```

Adds the phrase to the associated verb's phrases list in `drill_pool.json`. Sets status to `drilling`. Only works when `verb_hint` matches a core verb.

---

## Data Model — `phrasebook.json`

**Location:** `_NewDomains/language-german/language/german/config/phrasebook.json`

```json
{
  "phrases": [
    {
      "id": "ph_20260512_001",
      "german": "Nein danke, ich schaue nur.",
      "english": "No thanks, I'm just looking.",
      "scene": "shopping",
      "added": "2026-05-12",
      "status": "library",
      "verb_hint": "schauen",
      "practice_count": 0,
      "last_practiced": null
    }
  ]
}
```

**Status lifecycle:** `library` → `drilling` | `archived`  
**id format:** `ph_YYYYMMDD_NNN` (date + sequence within that day)  
**verb_hint:** optional — links phrase to a core verb for drill promotion. Not required for capture.

---

## What Is NOT in Scope (First Build)

- Scene tagging on capture — too much friction on mobile, inferred from verb_hint or left blank
- Multi-round practice sessions — user calls `!phrase practice` again manually
- Retire flow (phrase back from drill to library)
- Phrasebook browsing by scene
- Voice capture ("!phrase" via voice note)

---

## Files to Build

| File | Change |
|------|--------|
| `phrasebook.json` | Create new, seed with "Nein danke, ich schaue nur." |
| `telegram_bot.py` | Add `!phrase` routing + 5 handler functions (~100 lines) |

`drill_pool.json` — touched only by `!phrase drill` promote action.  
`_active_drill_state.json` — no structural change.

---

## Detailed Build Plan for Claude Code

### 1. Create `phrasebook.json`

Seed file at `GERMAN_DIR / "config" / "phrasebook.json"` with the first real phrase:

```json
{
  "phrases": [
    {
      "id": "ph_20260512_001",
      "german": "Nein danke, ich schaue nur.",
      "english": "No thanks, I'm just looking.",
      "scene": "shopping",
      "added": "2026-05-12",
      "status": "library",
      "verb_hint": "schauen",
      "practice_count": 0,
      "last_practiced": null
    }
  ]
}
```

---

### 2. Add helper functions to `telegram_bot.py`

Insert after `_save_drill_pool()` (around line 988):

```python
_PHRASEBOOK_FILE = GERMAN_DIR / "config" / "phrasebook.json"
_phrase_practice: dict = {}  # chat_id → {"phrase_id": str}; in-memory, restart-safe


def _load_phrasebook() -> dict:
    if _PHRASEBOOK_FILE.exists():
        try:
            return json.loads(_PHRASEBOOK_FILE.read_text())
        except Exception:
            pass
    return {"phrases": []}


def _save_phrasebook(data: dict) -> None:
    _PHRASEBOOK_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _phrase_next_id(phrases: list, today: str) -> str:
    prefix = f"ph_{today.replace('-', '')}_"
    existing = [p["id"] for p in phrases if isinstance(p, dict) and p.get("id", "").startswith(prefix)]
    return f"{prefix}{len(existing) + 1:03d}"
```

---

### 3. Add command handler functions

Add before `handle_text_message()`:

**`_handle_phrase_command`** — top-level dispatcher:
- Splits `!phrase <rest>` on first word
- Known subcommands: `list`, `practice`, `drill`, `help`
- Anything else (including text with `|`) → `_handle_phrase_save`

**`_handle_phrase_save(update, raw)`**:
- Splits `raw` on `|` → `german`, `english`
- Generates id via `_phrase_next_id`
- Appends to phrasebook, saves
- Replies: `✅ Saved (#NNN)\n🇩🇪 ...\n🇺🇸 ...`

**`_handle_phrase_list(update, args)`**:
- Parses optional N from args (default 10)
- Shows last N phrases newest-first
- Format: `#NNN 🇩🇪 ... \n     🇺🇸 ... [status, N practices]`

**`_handle_phrase_practice(update, args)`**:
- If args: find phrase by id (full or short suffix)
- If no args: pick lowest `practice_count` among `status=library` phrases, tiebreak newest
- Stores `_phrase_practice[chat_id] = {"phrase_id": pid}`
- Sends German + English + "→ Type the German spelling:"

**`_handle_phrase_practice_answer(update, text)`**:
- Pops `_phrase_practice[chat_id]`
- Loads phrase, normalizes both strings (lowercase, strip punctuation)
- `SequenceMatcher` ratio ≥ 0.85 → correct; else → show correction
- Increments `practice_count`, sets `last_practiced`, saves

**`_handle_phrase_to_drill(update, args)`**:
- Finds phrase by id
- Looks for `verb_hint` in `core.verbs` of `drill_pool.json`
- If found: appends `{english, german}` to verb's phrases list, sets status → `drilling`
- If not found: tells user to check `verb_hint` or add manually

---

### 4. Add routing in `handle_text_message`

**Add phrase_practice intercept** — right after the lazy drill-state reload block (around line 1733), before the `if chat_id in _active_drills` block:

```python
# Phrase practice intercepts before drill state
if chat_id in _phrase_practice and not text.lower().startswith("!phrase"):
    await _handle_phrase_practice_answer(update, text)
    return
```

**Add `!phrase` routing** — after the `!german` branch (around line 1762):

```python
elif text.lower().startswith("!phrase"):
    await _handle_phrase_command(update, text)
```

---

### 5. Verification checklist

1. `!phrase Nein danke, ich schaue nur. | No thanks, I'm just looking.` → replies with `#001`, both sides
2. `!phrase list` → shows the entry
3. `!phrase practice` → bot sends German + English + prompt
4. Reply with correct German → `✅ Correct spelling.`; check phrasebook.json `practice_count` incremented
5. Reply with misspelling → shows correction; `practice_count` still increments
6. `!phrase drill 001` → adds phrase to schauen's phrases list in drill_pool.json; status → `drilling`
7. Existing `drill German zahlen` → unaffected
8. Existing `end` exits drill → unaffected
9. Active drill intercept not triggered by `!phrase practice` command

---

## Connection to Broader Language Domain

This feature is the **field capture layer** that the formal drill system never had. It reflects how language actually gets learned in immersive travel:

1. Real encounter → capture
2. Library accumulates over the trip
3. Review and spelling-check at the end of the day
4. Selectively promote into formal drill for deep practice

The verb drill system (Level 1 conjugation, Level 2 translation) is for systematic study. The phrase library is for opportunistic in-context learning. Both are needed.

---

*Feature spec written 2026-05-12. Shipped 2026-05-12. 49/49 tests passing.*

---

## Amendment 1 — Correction + Confirmation Before Save

**Status: Shipped — 2026-05-12**

Original design saved the phrase as-entered. Field use revealed that voice capture and fast mobile typing produce errors. Amendment adds an LLM correction step before saving:

1. User submits phrase (text or voice)
2. Bot sends LLM-corrected version back: `Did you mean: 🇩🇪 [corrected] / 🇺🇸 [translation]?`
3. User confirms (ja/yes/✓) or rejects — only confirmed phrases are saved

This keeps the library clean without adding friction to the capture moment.

---

## Amendment 2 — Two-Step Voice Phrase Capture

**Status: Shipped — 2026-05-12**

Original design was text-only. Amendment adds full voice capture (zero typing):

1. Send voice note with the German phrase
2. Bot transcribes, LLM-corrects, sends confirmation prompt
3. User replies with a second voice note (or text) to confirm

Command consistency: text and voice commands work identically across all `phrase` subcommands.

---

## Post-Vienna Field Testing — UX Fixes (2026-05-12)

The following changes came directly from live field use in Vienna. All shipped in the same session as voice capture.

**Command consistency** — Text and voice commands now work identically:

| Command | Does |
|---------|------|
| `phrase add german \| english` | Save with both sides |
| `phrase add` | Two-step: bot prompts, LLM translates |
| `phrase list` | Last 10 phrases, newest first |
| `phrase more` | Next 10 (paginated) |
| `phrase practice` | Drill least-practiced phrase |
| `phrase practice 003` / `phrase practice three` | Drill specific phrase by ID |

**Auto-detect practice** — Typing or saying a German phrase that closely matches a library entry is scored automatically without opening a practice session.

**Paginated list** — `phrase list` shows last 10; `phrase more` pages through the rest.

**Drill exits on capture trigger** — If a verb drill is active when capture mode is triggered, the drill exits cleanly.

**Transcription noise tolerance** — Added regex variants for common misrecognitions: "Ad phrase" → add, "phase practice" → phrase practice, both word orders.

**Confirm set expanded** — Added Austrian/informal affirmatives.

**Practice hint** — `phrase list` footer now shows the next-step command.

**Stricter spelling match** — Fuzzy threshold raised 0.85 → 0.92. Single-character verb-ending differences now count as wrong.

**Bug fixed: Drill state not persisted on end** — `_finish_drill` was removing the drill from memory but not writing to disk. Fixed by adding `_save_drill_state()` to `_finish_drill`.
