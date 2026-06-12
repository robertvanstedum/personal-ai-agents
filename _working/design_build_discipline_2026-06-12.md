# Design — CoS Build Discipline
*mini-moi · Guild*
*Created: 2026-06-12 06:28 CDT — Claude.ai*
*Last revised: 2026-06-12 06:45 CDT — Claude.ai*
*Audience: Robert · Grok · OpenClaw — design review, not a build spec*
*Status: DESIGN — mechanics need agreement before any build spec is written*

**Revision history:**
- v0.1 (06:28) — initial design: state model, mechanics, model usage, schema
- v0.2 (same session) — added Build Clarity queue (table + Kanban), resolved
  staleness threshold to 3 days
- v0.3 (06:45) — incorporated Grok's three refinements: explicit `start-build`
  signal, transitions audit log, manual override mechanism
- v0.4 (06:56) — Grok final review, three polish items: `spec_title` column
  (Build Log/Queue display), numbered completeness-check prompt, documented
  config comment. Implementation detail in build spec; schema list below
  updated for consistency.

---

## Grok review — three refinements incorporated

Grok's review (2026-06-12) confirmed the design and flagged three high-impact
refinements, all incorporated below:

1. **Explicit `start-build` signal replaces commit-message scanning** — more
   reliable than regex on commit messages, costs Claude Code one command.
2. **Transitions audit log** — every status change becomes a recorded event
   (`from_status`, `to_status`, `triggered_by`, `reason`, timestamp). Beyond
   the portfolio value Grok notes, this is increasingly a baseline expectation
   for agentic systems in 2026 — when a system can change its own state,
   "why did it change" needs to be answerable without reconstructing from
   conversation history. This is the explainability/audit-trail pattern, applied
   to Guild's own build process.
3. **`incomplete` gets an explicit override path** — folded into a general
   "manual status change with optional note" mechanism (below), which also
   handles resolving `blocked` items. One mechanism, two use cases — avoids
   adding process for each case separately (Grok's "process overhead creep"
   risk, taken seriously).

---

## What problem this solves

All session, build state has been tracked by conversation and by me (Claude.ai)
manually: writing "Definition of Done" checklists, confirming items as CLOSED,
writing build log entries, telling Robert what's in flight vs. spec-ready vs.
deferred. This works, but it's not *in* the solution — it's a process Robert
and I do, not something Guild itself knows.

Build discipline formalizes this: the lifecycle a piece of work goes through —
design → spec ready → in build → done (or blocked/deferred) — becomes state
the system tracks, and CoS surfaces it. This is also the most concrete
"AI-augmented delivery process" artifact in the portfolio story: design agent,
build agent, process agent, with Robert as the human checkpoint — visible and
running, not described.

---

## The state model

Five states. These map directly to the Guild Daily Briefing "Build" section
already designed in `docs/GUILD.md` — this design is what makes that section's
data real.

```
DESIGN ──▶ SPEC READY ──▶ IN BUILD ──▶ DONE
              │               │
              ▼               ▼
           BLOCKED         BLOCKED
              │               │
              ▼               ▼
          DEFERRED        DEFERRED
```

| State | Meaning | Daily Briefing row |
|---|---|---|
| `design` | Being discussed, no doc yet — not tracked as a row, just conversation | — |
| `spec_ready` | A doc exists in `_working/`, passes completeness check | "Queued" |
| `in_build` | Claude Code has started work referencing this spec | "Active" |
| `blocked` | Needs a decision from Robert before it can proceed | "Needs decision" |
| `done` | Confirmed complete, build log entry exists | (drops off, moves to build log) |
| `deferred` | Explicitly parked — moved to `_working/archive/` or noted as deferred | "Deferred" |

Only `spec_ready`, `in_build`, `blocked`, and `deferred` are tracked as rows.
`design` is pre-system (conversation only) — nothing to track until a doc exists.
`done` is the exit state — it becomes a build log entry and disappears from
active tracking.

---

## Mechanics — how state actually changes

This is the part that needs to be concrete, because "CoS tracks build state"
is meaningless unless something specific causes a transition.

### `design → spec_ready` (automatic, existing infrastructure + one addition)

The Design/Dev agent already does this (Phase 5, Level 1): file watcher
detects a new/modified file in `_working/`, Haiku classifies it, logs to
`guild.design_log`.

**New addition — completeness check, same Haiku call:**

When Design/Dev classifies a doc as `handoff` or `spec`, it also checks for
three things (extend the existing classification prompt, no new LLM call):

1. Does the doc have a "Definition of Done" section?
2. Does it have a "Commit" section with concrete file paths?
3. Do files it references in `_working/` actually exist? (uses the Item 4
   gap-check logic — `scripts/check_handoff_gaps.py`)

**If all three pass:** `guild.design_log` row gets `status = 'spec_ready'`.
**If any fail:** `status = 'incomplete'`, Design/Dev notifies Robert and
Claude.ai with which check failed — *not* a silent failure, a specific flag
("missing Definition of Done" / "references spec_career_x.md, not found in
_working/").

This is the "enforce handoff completeness" piece, mechanically: a checklist
applied at classification time, by the agent that's already reading the doc.

### `spec_ready → in_build` (explicit signal, not passive detection)

**Grok's catch:** commit-message scanning is fragile — relies on Claude Code
consistently naming the spec in every commit, and regex against free-form
text. Replace with an explicit, cheap signal.

**Mechanically:** Design/Dev adds a small endpoint (`POST /start-build`,
alongside its existing `/status` endpoint on port 8770). Claude Code calls it
once at the start of work on a spec:

```bash
curl -s -X POST localhost:8770/start-build \
     -d '{"spec_file": "spec_career_two_page_2026-06-11.md"}'
```

(Or a thin wrapper script — `scripts/start_build.sh <spec-filename>` — for
convenience.) This flips `status = 'in_build'`, sets `last_transition_at`,
and writes a transition record (see below).

**One command, costs Claude Code nothing meaningful** — far more reliable
than parsing commit history, and it's the same kind of small convention as
the `!dev` commands already in use.

**Safety net if forgotten:** the 3-day staleness check still catches a spec
that's actually being worked on but never got the signal — it'll show as
"Spec Ready, 3+ days" and surface as needing attention either way. Not a
silent failure, just a slightly delayed one.

### `in_build → done` (Robert confirms, Design/Dev drafts)

When Claude Code reports a Definition of Done is complete (as has happened
all session — "all checks pass," "Robert sign-off confirmed"):

1. Design/Dev drafts a build log entry from: the original spec's Definition
   of Done checklist + the commit history since `in_build` began. Haiku
   summarizes.
2. Drafted entry goes to Robert (Telegram or portal) for confirmation —
   same visual sign-off pattern already in use.
3. On confirmation: `status = 'done'`, entry appended to
   `docs/GUILD_BUILD_LOG.md`, item drops off active tracking.

This doesn't remove Robert's sign-off — it removes *my* manual step of
writing the build log entry by hand each time.

### `→ blocked` (explicit or staleness-detected)

Two paths:

1. **Explicit** — Claude Code or Claude.ai marks an item blocked with a
   reason ("needs Robert's decision on X"). Simple: a status update via
   existing `!dev` command pattern.
2. **Staleness-detected** — CoS's build-discipline check (daily, part of
   the existing Loop F cadence) flags any `spec_ready` item with no
   `in_build` transition after N days, or any `in_build` item with no
   commit activity after N days, as needing attention. This surfaces as
   "needs decision" even if no one explicitly flagged it.

`N` is configurable in `cos_context.json` — start with something generous
(7 days) since this is new; tighten later if useful.

### `→ deferred` (explicit, existing lifecycle)

Design/Dev already manages `_working/` lifecycle (archive on supersession).
Moving a doc to `_working/archive/` with a note, or Robert/Claude.ai
explicitly marking an item deferred, sets `status = 'deferred'`. Drops off
active tracking, stays in `guild.design_log` for history.

### Manual override — `incomplete` and `blocked` resolution (one mechanism)

Both cases need the same thing: Robert changes an item's status by hand,
optionally with a note explaining why.

- **`incomplete → spec_ready`**: the completeness check flagged a missing
  section, but Robert decides the spec is fine as-is (e.g. "DoD not needed,
  this is a two-line fix"). Override, with a note.
- **`blocked → spec_ready` or `in_build`**: the decision Robert was needed
  for has been made; work can resume.

**Mechanically:** same status-dropdown pattern already used on Career's cards
— the Queue page cards get the same control. Changing status manually writes
a transition record with `triggered_by = 'robert'` and the optional note as
`reason`. No new UI pattern, no new Telegram command needed for v0.1.

### Transitions audit log

Every status change — automatic or manual — writes a row:

```sql
CREATE TABLE IF NOT EXISTS guild.design_log_transitions (
    id SERIAL PRIMARY KEY,
    design_log_id INTEGER REFERENCES guild.design_log(id),
    from_status TEXT,
    to_status TEXT NOT NULL,
    triggered_by TEXT,   -- 'design_dev' | 'cos_staleness' | 'robert' | 'claude_code'
    reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

This makes `guild.design_log` + `guild.design_log_transitions` a proper audit
log: not just "what state is this in" but "how did it get here, and who/what
decided." Cheap — one INSERT alongside each status UPDATE, no LLM involved.
Becomes the data source if/when a Grok build-state-auditor (v0.2) gets built.

---

## What CoS (build discipline mandate) actually does

CoS does not re-implement any of the above. It **reads** `guild.design_log`
(with the new `status` field) and:

1. **Populates the Daily Briefing "Build" section** — group by status:
   Active (`in_build`), Queued (`spec_ready`), Needs Decision (`blocked`),
   Deferred. This is the data layer for the Build section already designed
   in `docs/GUILD.md`.

2. **Runs the staleness check** (daily, Loop F cadence) — the only "new
   logic" CoS itself performs: scan for items past the configurable
   threshold, flag as `blocked` if not already.

3. **Surfaces in `/chat`** — "what's in flight" becomes a real answer
   (`!cos status` or natural language), not something only Claude.ai knows
   from conversation memory.

Everything else — classification, completeness check, drafting build log
entries — is Design/Dev's job, extended slightly. CoS sits on top and makes
it visible.

---

## Where Grok fits

**v0.1: exactly where Grok is right now — reviewing this design document.**

No runtime role for Grok in build discipline v0.1. The temptation is to wire
Grok into the loop as a "build state auditor" (Challenger-pattern style:
does the tracked state match reality?). That's a real idea, but it's v0.2 —
adding it now means designing a new cross-provider check before the basic
state model has even run for a week.

If, after v0.1 is live, the tracked state and reality drift in ways the
staleness check doesn't catch, *that's* the trigger for a Grok-audit version.
Not before.

---

## Model usage — concrete mapping

| Function | Model | Why |
|---|---|---|
| Doc classification + completeness check | Haiku | Already the pattern (Design/Dev), cheap, mechanical |
| Commit-message scan for state transition | None (regex/string match) | No judgment needed |
| Build log entry drafting | Haiku | Summarization from structured input (DoD checklist + commits) |
| Daily Briefing Build section | Whatever generates the Daily Briefing overall | Data assembly, not new generation — feeds existing (not-yet-built) briefing |
| Staleness check | None (date comparison) | Mechanical |
| `/chat` "what's in flight" | CoS's existing `/chat` chain (Grok→Haiku→mistral) | Already exists, just reads new data |

**Net new LLM calls: zero.** The completeness check extends an existing Haiku
call (classification). Build log drafting uses Haiku for a new but simple
summarization. Everything else is data reads and string/date comparisons.

---

## Schema additions

```sql
ALTER TABLE guild.design_log ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'spec_ready';
ALTER TABLE guild.design_log ADD COLUMN IF NOT EXISTS spec_file TEXT;
ALTER TABLE guild.design_log ADD COLUMN IF NOT EXISTS spec_title TEXT;
ALTER TABLE guild.design_log ADD COLUMN IF NOT EXISTS last_transition_at TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE guild.design_log ADD COLUMN IF NOT EXISTS blocked_reason TEXT;
ALTER TABLE guild.design_log ADD COLUMN IF NOT EXISTS github_issue TEXT;
```

`status` values: `spec_ready | in_build | blocked | done | deferred | incomplete`.
`incomplete` is a sub-state of spec_ready that failed the completeness check —
shows up differently in the briefing ("1 handoff incomplete: missing DoD").

---

## What this formalizes (portfolio note)

Right now: Claude.ai tracks build state via conversation memory and writes
it up by hand. After this: the same lifecycle — design, spec, build, done,
with explicit human checkpoints — is system state that any of the agents
(or Grok, or a future hire looking at this repo) can query and understand
without reading the conversation history.

This is the translation-doc row: "AI-augmented delivery process — design,
build, review, and process agents with human checkpoints" stops being a
description and becomes something you can point at in `guild.design_log`.

---

## Build Clarity — the queue (table + Kanban)

This is the answer to "how do I see this." Without a page, the state model
is invisible except via `/chat` or a one-line Daily Briefing summary —
not enough for something Robert will check daily.

**Same two-page pattern as Career** — table for full inventory/reporting,
Kanban for what's currently moving. Reuse the card/column/collapsible
patterns already built for Career rather than redesigning.

### Page 1 — Build Log (`/guild/build`, table, default)

Full inventory from `guild.design_log`: every spec, every status, including
`done` and `deferred`. Sortable, filterable by status. This is also the
reporting surface — "how many specs went from spec_ready to done last week,"
"average time in_build," etc. — same role the Positions table plays for Career.

Columns: title, status, age (`NOW() - last_transition_at`), GitHub issue
(if linked), link to the spec file in `_working/` or `_working/archive/`.

### Page 2 — Queue (`/guild/build/queue`, Kanban)

Only items currently in motion — mirrors Career's Active Pipeline filter logic:

```sql
status IN ('spec_ready', 'in_build', 'blocked')
OR (status = 'done' AND last_transition_at > NOW() - INTERVAL '3 days')
```

**Four columns:**

| Column | Status | What it means |
|---|---|---|
| Spec Ready | `spec_ready` | Queued, ready for Claude Code |
| In Build | `in_build` | Active work |
| Blocked | `blocked` | **Needs Robert's decision** — the equivalent of Career's starred-applied, the column that matters most |
| Recently Done | `done` (within grace period) | Just completed, drops off after 3 days — stays in Build Log permanently |

**`incomplete` items** don't get their own column — they show as a tag on
their Spec Ready card: `⚠ incomplete · missing DoD` (mirrors how Career shows
`Closed · Filled` as a tag, not a column). Same for `blocked_reason` on
Blocked cards. Both resolvable via the status dropdown — see "Manual override"
above.

**Deferred items** don't appear on the board at all — visible only in the
Build Log table, same as Career's pattern where rejected/deferred items are
table-only.

Collapsible cards (same `···`/`▲` pattern as Career): collapsed shows title +
status + age; expanded shows the spec summary (from Design/Dev's
classification), `blocked_reason` if applicable, GitHub link if linked.

### GitHub link

**Page-level:** "View GitHub Issues →" link in the header, to the repo's
issues page generally — for cross-referencing what's tracked in `guild.design_log`
against what's tracked in GitHub (the sync question from an earlier session,
still not auto-synced, just easy to cross-check).

**Per-card (optional):** new nullable field `github_issue` on `guild.design_log`
(e.g., `"#41"`). When set — either by Claude.ai when writing a spec that
corresponds to an issue, or manually — the card shows a small GitHub icon/link
to that issue. Not required; most specs won't have one, and that's fine.

### Navigation

```html
<div class="guild-subnav">
    <a href="/guild/build">Build Log</a>
    <a href="/guild/build/queue">Queue</a>
</div>
```

The Daily Briefing's "Build" section (Active / Queued / Needs Decision /
Deferred rows) links each row to `/guild/build/queue` filtered to that status
— the briefing is the summary, the Queue page is where you go to act on it.

---

## v0.1 scope — what's NOT included

- Grok runtime audit role (deferred, as above) — `design_log_transitions`
  is the data it would use, but the audit logic itself is v0.2
- Cross-referencing GitHub issues with `guild.design_log` beyond the
  optional `github_issue` field and page-level link (deeper sync is a
  separate question from earlier, not part of this)
- Any change to how Claude.ai writes specs — the spec format stays the same,
  just needs the two sections (DoD, Commit) which is already near-universal
  practice in this session's handoffs
- A dedicated `!dev` Telegram command for manual overrides — the Queue page
  dropdown covers v0.1; a Telegram shortcut can be added later if the UI
  proves inconvenient

**In v0.1** (confirmed after Grok review): explicit `start-build` signal,
transitions audit table, manual override via status dropdown (covers both
`incomplete` and `blocked` resolution). All three are small additions to
what was already scoped — no new agents, no new LLM calls.

---

## Staleness threshold — resolved: 3 days

Given this session's actual pace (most items go spec_ready → done within a
day), 3 days is the right starting threshold — generous enough not to flag
normal in-progress work, tight enough to catch something that's actually
stalled. Set in `cos_context.json`:

```json
{
  "build_discipline": {
    "staleness_days": 3,
    "recently_done_grace_days": 3
  }
}
```

`recently_done_grace_days` reuses the same number for the Kanban's Recently
Done column (see below) — one config, two uses, easy to split later if they
need to diverge.

---

*Build Discipline design · Guild · 2026-06-12*
*Includes Build Clarity queue (table + Kanban) and Grok's three refinements*
*Status: design agreed (Grok: "proceed to build spec") — ready for build spec*
