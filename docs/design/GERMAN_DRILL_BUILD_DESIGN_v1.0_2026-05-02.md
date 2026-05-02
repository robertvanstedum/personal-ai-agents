# mini-moi — German Domain
## Build Design: Drill Mode + Georg Persona + Scaffold Tracking
### v1.0 — All 5 Steps

| Field | Value |
|---|---|
| Date | 2026-05-02 |
| Author | Robert van Stedum + Claude Code |
| Status | Draft — Steps 1–2 built. Steps 3–5 pending review. |
| Vienna departure | May 10, 2026 — 8 days |
| Prior design | GERMAN_DRILL_BUILD_SPEC_v0.2_2026-05-02.md |

---

## Step 1–2 — Georg Persona + drill_pool.json + Scaffold Bridge
**Status: Built and committed. 31/31 tests passing.**

### What was built

**Georg persona in `keyword_map.json`**

New persona for casual local conversation — fills the social gap between transactions. Trigger words: `local`, `georg`, `chat`, `small talk`, `conversation`, `heuriger`, `würstelstand`, `casual`. Default scenario: `local_smalltalk`.

Six scaffold phrases (rotate 2 per session, same as all personas):

| German | English | Type |
|---|---|---|
| Ich komme aus Chicago — kennen Sie die Stadt? | I'm from Chicago — do you know the city? | transaction |
| Ich möchte lieber etwas sehen, was Touristen nicht kennen. | I'd prefer to see something tourists don't know. | preference |
| Was empfehlen Sie für einen Samstagmorgen? | What do you recommend for a Saturday morning? | preference |
| Wir sind länger in Wien — nicht nur ein Tag. | We're in Vienna for a while — not just one day. | transaction |
| Ich lerne Deutsch — bitte sprechen Sie ruhig normal. | I'm learning German — please just speak normally. | transaction |
| Das klingt wunderbar — wo genau ist das? | That sounds wonderful — where exactly is that? | preference |

Recovery: *Entschuldigung — ich verstehe, aber ich suche die Antwort auf Deutsch.*

**Georg ready answers (shown in Message 1 briefing only):**

Georg sessions show a `💬 Ready answers:` block with Robert's pre-loaded responses to common small-talk questions:

| Question | Answer |
|---|---|
| Woher kommen Sie? | Ich komme aus Chicago — aus den USA. |
| Was machen Sie beruflich? | Ich bin Produktmanager — Technologie und Telekommunikation. |
| Wie lange bleiben Sie in Wien? | Wir bleiben etwa eine Woche. |
| Ist das Ihr erster Besuch? | Nein, ich war schon einmal hier — aber das ist lange her. |
| Sprechen Sie Deutsch? | Ein bisschen — ich lerne noch. Aber ich versuche es gerne. |
| Reisen Sie oft? | Ja, ich reise sehr gern — Brasilien, Dubai, viele Länder. |

**`drill_pool.json` created** with Tier 1 core data:
- 10 core verbs: können, möchten, haben, sein, nehmen, fahren, gehen, empfehlen, brauchen, zahlen
- 10 nouns with article (gender) and scene tags — mixed der/die/das
- 2 pattern entries (article case grid, Viennese café orders)
- Empty `session_fed.pending`, `session_fed.approved`, `on_demand.log` arrays

**Scaffold delivered bridge:** `get_german_session.py` now writes `last_scaffold_delivered` to `progress.json` after every session — a 3-string list (2 scaffold phrases + recovery phrase). This is what `reviewer.py` reads in Step 3.

---

## Step 3 — Scaffold Tracking in reviewer.py
**Status: Not built. Pending review.**

### What it does

After `reviewer.py` processes a session transcript, it checks which scaffold phrases Robert actually used and which he avoided. Closes the loop between what was prepared and what was deployed.

### Data flow

```
get_german_session.py
  └─ _scaffold_block() writes last_scaffold_delivered → progress.json
        ["Ich hätte gerne einen kleinen Brauner, bitte.",
         "Was empfehlen Sie heute?",
         "Einen Moment bitte — ich überlege kurz."]

reviewer.py (new logic)
  └─ reads last_scaffold_delivered from progress.json
  └─ copies it into session JSON as scaffold_delivered
  └─ for each phrase, searches Robert's turns only:
       exact match (case-insensitive) → scaffold_used
       root match (first 6 chars, catches inflections) → scaffold_used
       not found → scaffold_avoided
  └─ writes scaffold_deployment_rate string
  └─ flags scaffold_avoided as ⭐ Anki candidates in Telegram output
```

Root match example: scaffold phrase starts "empfeh" → matches "empfiehlt", "empfohlen", "Empfehlung" in Robert's text.

### New session JSON fields

| Field | Example value |
|---|---|
| `scaffold_delivered` | `["Ich hätte...", "Was empfehlen...", "Einen Moment..."]` |
| `scaffold_used` | `["Ich hätte gerne einen kleinen Brauner, bitte."]` |
| `scaffold_avoided` | `["Was empfehlen Sie heute?", "Einen Moment..."]` |
| `scaffold_deployment_rate` | `"1/3 scaffold phrases used"` |

### Telegram output addition

```
── Scaffold ──────────────────────────
Used (1/3):  Ich hätte gerne einen kleinen Brauner, bitte.
Avoided:     Was empfehlen Sie heute?  ⭐ Anki candidate
             Einen Moment bitte — ich überlege kurz.  ⭐ Anki candidate
```

### Promotion to drill pool

- Avoided 1 session → flagged in Telegram output only
- Avoided 2+ consecutive sessions, same persona → promoted to `drill_pool.json` `session_fed.pending`

Consecutive check: look at last 2 session JSONs for the same persona. If the phrase appears in `scaffold_avoided` in both → promote.

### Backward compatibility

Old session JSONs without `scaffold_delivered` → `.get()` with empty list → analysis skipped, no crash.

### Files changed

- `reviewer.py` only

### Tests to add

- `scaffold_used` populated when phrase appears in Robert's turns (exact match)
- `scaffold_used` populated via root match (inflected form)
- `scaffold_avoided` populated when phrase not deployed
- `scaffold_avoided` × 2 consecutive sessions, same persona → promoted to `drill_pool.json` pending
- Old session JSON without `scaffold_delivered` → no crash
- Telegram output contains deployment rate string

---

## Step 4 — Drill Intent Routing in telegram_bot.py
**Status: Not built. Pending review.**

### What it does

Adds drill-specific intent detection to the Telegram bot. `conjugate [verb]` triggers Level 0 (fully live). `drill [anything]` triggers the drill handler (Levels 1–2 return a placeholder in this step, live in Step 5). Routing only — no drill engine yet.

### New regex patterns

```python
_CONJUGATE_RE = re.compile(r'\bconjugate\s+(\w+)\b', re.I)
_DRILL_RE     = re.compile(r'\bdrill\s+(.*)', re.I)
_DRILL_CTL_RE = re.compile(r'\b(enough|got it|next|done|stop|end drill)\b', re.I)
```

Routing order (after existing SESSION / AGAIN / keyword intent checks):

```
1. SESSION trigger  → existing handler
2. AGAIN trigger    → existing handler
3. keyword intent   → existing handler
4. CONJUGATE_RE     → _handle_conjugate(verb)      ← NEW (live)
5. DRILL_RE         → _handle_drill(target)         ← NEW (placeholder)
6. DRILL_CTL_RE     → _handle_drill_control(word)   ← NEW (placeholder)
7. fallback         → existing fallback
```

### Level 0 — Conjugate (live in Step 4)

**Trigger:** `conjugate können` / `conjugate sein` / `conjugate [verb]`

**Response format:**

```
können — fill in all persons:
ich ___    wir ___
du ___     ihr ___
er ___     sie/Sie ___
```

Verb table hardcoded for the 10 core verbs. Any other verb → `"Conjugation for [verb] not in core list — add it to drill_pool.json to drill it."`

**Core verb table:**

| Verb | ich | du | er/sie | wir | ihr | sie/Sie |
|---|---|---|---|---|---|---|
| können | kann | kannst | kann | können | könnt | können |
| möchten | möchte | möchtest | möchte | möchten | möchtet | möchten |
| haben | habe | hast | hat | haben | habt | haben |
| sein | bin | bist | ist | sind | seid | sind |
| nehmen | nehme | nimmst | nimmt | nehmen | nehmt | nehmen |
| fahren | fahre | fährst | fährt | fahren | fahrt | fahren |
| gehen | gehe | gehst | geht | gehen | geht | gehen |
| empfehlen | empfehle | empfiehlst | empfiehlt | empfehlen | empfehlt | empfehlen |
| brauchen | brauche | brauchst | braucht | brauchen | braucht | brauchen |
| zahlen | zahle | zahlst | zahlt | zahlen | zahlt | zahlen |

### Drill handler (placeholder in Step 4)

`drill [anything]` → confirms routing, returns placeholder:

```
Drill mode coming soon.
You entered: "tatsächlich"
```

### Compatibility rules

- `conjugate` and `drill` do not advance lesson rotation
- `again` after a drill → repeats last non-drill session persona (existing behavior unchanged)
- All 31 existing tests pass after Step 4

### Files changed

- `telegram_bot.py` only

### Tests to add

- `conjugate können` → table returned, all 6 forms present
- `conjugate sein` → table returned
- `conjugate unknownverb` → "not in core list" message
- `drill tatsächlich` → placeholder returned, does not trigger session
- `drill my mistakes` → drill handler fires
- `conjugate` / `drill` do not advance lesson rotation

---

## Step 5 — drill_session.py: Level 1 (Lock) + Level 2 (Vary)
**Status: Not built. Pending review.**

### What it does

Full drill engine. New file `drill_session.py`. Level 1 locks a word in one template with learner-controlled reps. Level 2 varies the noun (gender shifts → article changes). Recall from `on_demand.log`. Session JSON written with `mode: drill`.

### Three levels recap

```
Level 0 — Conjugate   →   Level 1 — Lock   →   Level 2 — Vary
Pure verb table            Word in one phrase     Rotate noun or person
Live in Step 4             ~10 reps, learner led  Forces case/article shift
```

All three are independently triggerable. Natural progression for a new verb: 0 → 1 → 2. Jump in at any level.

### Level 1 — Lock

**Template inference from input:**

| Input | Type | Template |
|---|---|---|
| `drill können` | verb (in core list) | `"Können Sie mir ___ bringen?"` |
| `drill tatsächlich` | adverb | `"Tatsächlich, ___."` |
| `drill Gepäck` | noun (found in drill_pool) | Accusative frame for that gender |
| `drill "Darf ich das Gepäck hier lassen?"` | quoted phrase | Use as-is |
| unrecognized word | unknown | Bot asks: "verb, noun, or expression?" |

Detection order: core verbs list → core nouns list → quoted phrase → ask.

**Session flow:**

```
User:  drill tatsächlich
Bot:   Template: "Tatsächlich, ___." — go. (1×)
User:  Tatsächlich, das Zimmer ist sehr schön.
Bot:   ✅ (2×)
User:  Tatsächlich, ich möchte lieber früher auschecken.
Bot:   ✅ (3×)
User:  enough
Bot:   Saved. Next word, or done?
```

Rep count shown after each response as `(3×)`. Not enforced. Learner signals when done.

**Grading:**

| Element | Check |
|---|---|
| Verbs | Conjugation correct for subject used |
| Nouns | Article and case correct |
| Adverbs/expressions | Position correct in sentence |
| Word order | Verb second in main clause |

Feedback: ✅ or ❌ with correction inline. No explanation unless Robert asks.

### Level 2 — Vary

**Trigger examples:**

| Input | What rotates |
|---|---|
| `drill können phrases` | Fix können frame, rotate nouns across genders |
| `drill hotel nouns` | Fix hotel scene frame, rotate hotel nouns |
| `drill mit dative` | Fix mit + frame, rotate nouns → dative articles |
| `drill café orders` | Nominalized adjectives — einen Kleinen Braunen, etc. |

Noun selection: pull from `drill_pool.json` core nouns. Minimum one der, one die, one das per session to force article variation.

**Example — können + accusative nouns:**

```
Bot:  Können Sie mir ___ bringen? — vary the noun. (1×)
      Nouns: Schlüssel (m), Rechnung (f), Gepäck (n)
User: Können Sie mir den Schlüssel bringen?
Bot:  ✅ den ✅ verb (2×)
User: Können Sie mir die Rechnung bringen?
Bot:  ✅ (3×)
User: Können Sie mir das Gepäck bringen?
Bot:  ✅ (4×)
User: enough
Bot:  Saved. Next?
```

### Interaction mechanics

| Input | What happens |
|---|---|
| `enough` / `got it` / `next` | Ends word. Bot asks: next word or done? |
| `drill [new word]` | Switches immediately. Current word saved. |
| `done` / `stop` / `end drill` | Closes session. Summary shown. |
| `again` (within drill) | Repeats last prompt |

### Recall

| Input | What happens |
|---|---|
| `drill tatsächlich again` | Pulls saved template from log, restarts from (1×) |
| `drill tatsächlich` | System sees it's in log, uses same template |
| `what did I drill today` | Lists on-demand log entries from today with rep counts |
| `drill my recent` | Redoes last 3 words drilled |
| `drill my mistakes` | Top 5 from session error logs |

### What gets saved

Per word, written to `drill_pool.json → on_demand.log`:

```json
{
  "word": "tatsächlich",
  "template": "Tatsächlich, ___.",
  "timestamp": "2026-05-02T14:32:00Z",
  "reps": 3,
  "level": "lock"
}
```

Not saved: individual responses, individual grades.

### Session JSON + Telegram summary

Drill sessions write a session JSON with `mode: drill`. Counts toward session total:

```
Sessions: 56 (voice: 32 · writing: 9 · drill: 3)
```

Telegram summary at end of drill session:

```
Done. Drilled today:
  tatsächlich — 3×  (lock)
  können — conjugation
  können phrases — 4×  (vary, hotel nouns)
```

Drill sessions do **not** advance lesson rotation.

### Files changed

- `drill_session.py` — new file, full Level 1 + Level 2 engine
- `telegram_bot.py` — replace Step 4 placeholder with real `drill_session.py` calls
- `drill_pool.json` — `on_demand.log` populated during sessions

### Tests to add

- Level 1: template inferred correctly for verb / noun / adverb
- Level 1: rep count displayed, not enforced
- Level 2: mixed-gender noun set pulled (at least one m/f/n)
- Level 2: article changes with each noun
- Recall: `drill [word] again` uses saved template
- `what did I drill today` returns log entries for today
- Session JSON written with `mode: drill`
- Drill does not advance lesson rotation

---

## Build Order Summary

| Step | Status | Files | Gate |
|---|---|---|---|
| 1–2 | ✅ Done | `keyword_map.json`, `drill_pool.json`, `get_german_session.py` | 31/31 passing |
| 3 | Pending | `reviewer.py` | 31 + scaffold tests pass |
| 4 | Pending | `telegram_bot.py` | 31 + routing tests pass |
| 5 | Pending | `drill_session.py`, `telegram_bot.py` | 31 + full drill tests pass |

Each step committed separately. All existing tests pass after every step before moving to the next.

---

## Open Questions for Review

1. **Root match threshold (Step 3):** 6 characters for inflection matching — right threshold? Short words (e.g. "sein") could produce false positives. Alternative: 5 chars, or skip root match for words under 6 chars.

2. **Conjugation source (Step 4):** Core 10 verbs are hardcoded. For verbs outside the list, the bot declines. Should it instead ask Grok to supply the table on the fly?

3. **Level 1 noun frame (Step 5):** When `drill Gepäck` is entered, does the bot default to accusative silently, or ask "accusative or dative frame?" first?

4. **Drill session JSON location (Step 5):** Drill sessions written to the same `sessions/` directory as voice/writing sessions. Should they go in a separate `drill_sessions/` folder to keep the history clean?

---

*mini-moi German Domain — Drill Build Design v1.0 — 2026-05-02 — Robert van Stedum + Claude Code*
