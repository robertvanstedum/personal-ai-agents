# Thinking Record — Build Plan
**Date:** 2026-03-22 (build starts 2026-03-23)
**Branch:** poc/thinking-record (cut from poc/research-source-quality)
**Spec:** docs/specs/thinking-record-2026-03-22.md
**Status:** Ready to build — prerequisites documented below

---

## Start Condition — Do Not Build Until These Are Met

1. **Robert writes three motivations** — empire-landpower, china-rise, gold-geopolitics.
   See format below. This is the hard prerequisite. Nothing gets built until these exist.

2. **Pydantic check** — feedback.py already uses Pydantic so it's likely installed, but confirm
   version before build starts:
   ```bash
   python3 -c "import pydantic; print(pydantic.__version__)"
   ```
   Must be ≥ 2.0 (spec uses `field_validator` and `model_dump()` which are v2 API).
   If missing or < 2.0: `pip install "pydantic>=2.0"`

3. **agent/__init__.py check** — confirm whether it exists:
   ```bash
   ls agent/__init__.py
   ```
   If missing, add empty file before building threads.py. Otherwise `from agent.threads import ...`
   will fail silently when research.py and observe.py try to import it.

---

## Robert's Motivations — Write These Before Morning

For each topic, two fields. Freeform prose. Be honest about what you believed before sessions
started — immutability only has value if the prior is genuine.

Format (just prose, Claude Code handles the JSON):

**empire-landpower**
- Motivation: why did you open this thread? What question were you trying to answer?
- Prior belief: what did you believe before seeing any evidence?

**china-rise**
- Motivation: …
- Prior belief: …

**gold-geopolitics**
- Motivation: why was gold dropping during Iran escalation counterintuitive to you?
  What was the specific puzzle you were trying to resolve?
- Prior belief: "Gold should rise during geopolitical stress. Safe haven demand should dominate."
  (from spec — confirm or revise)

---

## Gap Resolutions (from claude.ai review, 2026-03-22)

These gaps were flagged in the build plan review. Resolutions are locked in — no decisions
needed tomorrow morning.

**Gap 1 — agent/__init__.py**
Likely missing since agent scripts have been running as standalone files, not as a package.
Resolution: if missing, add empty `agent/__init__.py` as first action of build session.
OpenClaw to verify tonight.

**Gap 2 — thread_id format**
Resolution: auto-append year. Format: `{topic}-{year}` → `gold-geopolitics-2026`.
If a second thread opens on the same topic in the same calendar year: append month →
`gold-geopolitics-2026-09`. Claude Code implements this in `threads.py create` — no `--id`
flag needed, no Robert input required.

**Gap 3 — Retroactive session_count and last_session_date**
Resolution: `topics/{topic}/` directory is authoritative — same source as `get_topic_sessions()`
in the dashboard helper. Do NOT use session-log.md (markdown, harder to parse, inconsistent).
Consistent with existing code. Claude Code reads topic directory, counts session files,
reads most recent mtime for last_session_date.

**Gap 4 — --links-to flag on create**
Resolution: defer. No existing closed threads to link to yet. Adding it now would be
untestable. Implement when first thread closes and Robert opens a follow-on thread.
Flag as BACKLOG item in BACKLOG.md.

**Gap 5 — Pydantic version**
Resolution: confirm ≥ 2.0 is installed (see Start Condition above). The spec uses
`field_validator` and `model_dump()` which are Pydantic v2. Running an earlier version
produces import errors that will be silently caught by the graceful degradation guard —
making the Thinking Record invisibly non-functional.

---

## Build Sequence — Ordered Steps

### Step 1 — `agent/threads.py` (new file)
**What:** Core library and CLI for thread management.
**Pattern:** Follow `feedback.py` verbatim — Pydantic schemas, `_load`/`_save` helpers,
`_now()` UTC timestamp, argparse subcommands.

Pydantic schemas:
- `ThreadRecord` — maps exactly to thread.json schema in spec (lines 131-155)
- `Annotation` — maps exactly to annotations.json schema in spec (lines 159-187)

Library functions (imported by research.py and observe.py):
- `load_thread(topic)` / `save_thread(topic, record)`
- `load_annotations(topic)` / `save_annotations(topic, list)`
- `check_direction_shifts(topic)` → returns latest unprocessed shift dict or None
- `mark_influenced(annotation_id, session_id)` → updates influenced_sessions in annotations.json
- `get_active_direction(topic)` → same as check_direction_shifts, named for observe.py use

CLI commands:
```
python agent/threads.py create --topic gold-geopolitics
python agent/threads.py annotate --topic gold-geopolitics --type direction_shift --note "..."
python agent/threads.py annotate --topic gold-geopolitics --type reaction --ref-session gold-001 --note "..."
python agent/threads.py wrap-up --topic gold-geopolitics --note "..."
python agent/threads.py retire --topic gold-geopolitics --reason "..."
python agent/threads.py status --topic gold-geopolitics
python agent/threads.py list
```

Data path: `data/threads/{topic}/thread.json` and `data/threads/{topic}/annotations.json`
Directory creation: `mkdir(parents=True, exist_ok=True)` — same as feedback.py _save pattern.

**Depends on:** Nothing. Build this first.
**Commit:** `feat(threads): add threads.py — thread management library and CLI`

---

### Step 2 — Retroactive thread.json + annotations.json for three topics
**What:** Six new files. Claude Code creates them using threads.py CLI + Robert's motivation text.

Files created:
- `data/threads/empire-landpower/thread.json`
- `data/threads/empire-landpower/annotations.json` (empty list `[]`)
- `data/threads/china-rise/thread.json`
- `data/threads/china-rise/annotations.json` (empty list `[]`)
- `data/threads/gold-geopolitics/thread.json`
- `data/threads/gold-geopolitics/annotations.json` (empty list `[]`)

`last_session_date` and `session_count` populated from `topics/{topic}/` directory
(Gap 3 resolution above). `thread_id` auto-generated as `{topic}-2026` (Gap 2 resolution).

**Depends on:** Step 1 (threads.py exists) + Robert's motivations text.
**Commit:** `feat(threads): retroactive thread records for empire-landpower, china-rise, gold-geopolitics`

---

### Step 3 — Update `agent/research.py`
**What:** Two insertion points.

At session start (after config load, before search loop):
```python
from agent.threads import check_direction_shifts, mark_influenced
shift = check_direction_shifts(topic)
# inject shift["note"] into triage context if present
```

At session end (after scoring complete):
```python
if shift:
    mark_influenced(shift["id"], session_id)
```

Use `try/except ImportError` graceful degradation — if threads.py import fails, session
runs without direction context. Silent, not fatal.

**Depends on:** Step 1.
**Commit:** `feat(threads): inject direction shifts into research.py triage context`

---

### Step 4 — Update `agent/observe.py`
**What:** One insertion point before Sonnet synthesis call.

```python
from agent.threads import get_active_direction
direction = get_active_direction(topic)
if direction:
    # prepend to synthesis prompt:
    # "Researcher's current focus: {direction}\nSynthesize with this focus in mind."
```

Same graceful degradation guard as Step 3.

**Depends on:** Step 1.
**Commit:** `feat(threads): inject direction shifts into observe.py synthesis prompt`

---

### Step 5 — Add thread API routes to `research_routes.py`
**What:** Five new routes in `/Users/vanstedum/Projects/personal-ai-agents/research_routes.py`
(main curator server file — NOT inside _NewDomains).

Routes:
- `GET  /api/research/thread/<topic>` → thread.json + recent annotations
- `POST /api/research/thread/<topic>/annotate` → write annotation
- `POST /api/research/thread/create` → create new thread
- `POST /api/research/thread/<topic>/retire` → retire thread
- `POST /api/research/thread/<topic>/wrap-up` → close with conclusion

All route handlers call threads.py library functions. No direct JSON file access in routes.
Do NOT touch curator_server.py.

**Depends on:** Step 1.
**Commit:** (part of main repo commit, separate from research-intelligence commits)

---

### Step 6 — Validation run (Robert's gate)
**What:** Robert runs one full research session on gold-geopolitics, then adds a
direction_shift annotation via CLI, then runs observe.

Three things must work:
1. Annotation written to `data/threads/gold-geopolitics/annotations.json`
2. Next research.py session picks up the shift — `influenced_sessions` updated after run
3. Observe output reflects the direction focus

**This is the gate.** UI work (Steps 7-8) does not start until this passes.

---

### Step 7 — UI: annotation input on dashboard (Surface A)
**What:** Per-topic card in `web/dashboard.html` gets:
- Motivation display (read-only, from thread.json)
- Annotation textarea + type selector (direction_shift / reaction / observation) + submit
- Submit POSTs to `POST /api/research/thread/{topic}/annotate`

**Depends on:** Steps 1, 5, and Step 6 passing.

---

### Step 8 — UI: annotation input on observe page (Surface C)
**What:** Below synthesis output in `web/observe.html`:
- Annotation field
- "Does this change your direction?" — if yes, type auto-sets to direction_shift

**Depends on:** Steps 1, 5, and Step 6 passing.

---

### Deferred (not tomorrow)
- Surface B: session-level annotation UI — deferred until session view is designed
- Auto-close script (30-day inactivity check in run.py)
- `--links-to` flag on thread create (Gap 4 resolution — build when first thread closes)

---

## Commit Sequence Summary

All research-intelligence commits on branch `poc/thinking-record`:
1. `feat(threads): add threads.py — thread management library and CLI`
2. `feat(threads): retroactive thread records for three topics`
3. `feat(threads): inject direction shifts into research.py triage context`
4. `feat(threads): inject direction shifts into observe.py synthesis prompt`

research_routes.py commit goes on the main personal-ai-agents repo branch.

---

*Locked in: 2026-03-22*
*Build starts: 2026-03-23 morning*
*Spec reference: docs/specs/thinking-record-2026-03-22.md*
