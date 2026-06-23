# Handoff — New Documents + First Build Steps
*Created: 2026-06-16 — Claude.ai*
*Audience: Claude Code*
*Covers: 5 new docs to commit + first build specs in two areas*

---

## Context

This session produced two significant new directions alongside the ongoing
Gespräche and mobile work:

1. **Gespräche as a mobile-first, voice-native practice environment** —
   with a clear roadmap from near-term fixes through voice commands and
   automatic transcript handoff to learning system integration.

2. **mini-moi Learning System** — a structured approach to institutional
   knowledge: Decision Records capture design reasoning, a local LLM enters
   the team as a junior partner, RAG and LoRA are sequenced as signal
   accumulates.

Five documents were produced that need to be committed to the repo. Two
build specs are ready. Both are described below with exact file paths,
content descriptions, and commit instructions.

---

## Part 1 — Five documents to commit

### Document 1 — Learning System Roadmap

**Source file:** `docs_LEARNING_SYSTEM_ROADMAP_2026-06-16.md`
**Destination in repo:** `docs/LEARNING_SYSTEM_ROADMAP.md`

**What it is:** The GitHub-visible roadmap for how mini-moi builds
institutional knowledge over time. Explains the local LLM as institutional
memory, frontier models as consultants, and four phases:
- Phase 0: workflow habits now (Decision Records, design session prompt,
  local LLM evaluation) — no new infrastructure
- Phase 1: RAG layer (index repo + docs, wire to local LLM)
- Phase 2: LoRA training (design philosophy adapter + German error adapter)
- Phase 3: integrated local-first operation (~6 months)

This is a top-level project document. It belongs in `docs/` and should
be linked from `README.md`.

---

### Document 2 — Design Session Prompt

**Source file:** `docs_DESIGN_SESSION_PROMPT_2026-06-16.md`
**Destination in repo:** `docs/DESIGN_SESSION_PROMPT.md`

**What it is:** The prompt pasted at the start of any significant design
conversation — with Claude, Grok, OpenAI, or the local LLM. It shapes
how the LLM participates (naming alternatives, flagging constraints and
assumptions, distinguishing rejected vs. deferred) and produces a
structured Decision Record when Robert says "write decision record."

Shared flagging: the LLM flags proactively on seven specific criteria;
Robert can flag anything by saying "flag this" — his flag always wins.

This document is both a process document and a tool. It belongs in `docs/`
and should be referenced in `HANDOFF_PROCESS.md`.

---

### Document 3 — Gespräche Roadmap

**Source file:** `docs_GESPRACHE_ROADMAP_2026-06-16.md`
**Destination in repo:** `docs/GESPRACHE_ROADMAP.md`

**What it is:** The vision and roadmap document for Gespräche specifically.
Explains why mobile is the priority (phone is the natural device for
language practice), what's in the near-term build queue (referencing
existing specs by filename), the medium-term direction (automatic loop,
repeat-and-modify), voice commands, and the connection to the learning
system. Also explains where Lesen and Schreiben fit — useful but not the
mobile investment driver.

References `dr_gesprache_mobile_priority_2026-06-16.md` by filename —
that DR must be committed in the same commit or the reference will be
a dead link.

---

### Document 4 — Decision Record: Gespräche Mobile Priority

**Source file:** `dr_gesprache_mobile_priority_2026-06-16.md`
**Destination in repo:** `docs/decision-records/dr_gesprache_mobile_priority_2026-06-16.md`

**What it is:** The first Decision Record produced under the new design
session discipline. Captures the full reasoning behind two decisions made
in today's session: (1) Gespräche mobile as the German domain priority,
and (2) the learning system foundation approach.

Contains: decision summary, context, six alternatives with rejection
reasoning, constraints, assumptions, known failure modes, nine principles,
negative boundary ("what this is not"), flags from the session, and open
questions.

The `docs/decision-records/` directory does not yet exist in the repo.
It must be created in this commit. See Part 2 below.

---

### Document 5 — Design Session Prompt (same as Document 2)

*(Document 2 and Document 5 are the same file. Five documents total,
not six — the design session prompt was counted twice in the summary.
The five are: Learning System Roadmap, Design Session Prompt, Gespräche
Roadmap, Decision Record, and the foundation spec in Part 2.)*

---

## Part 2 — Build Spec A: Commit the foundation documents

**Spec file:** `spec_learning_system_foundation_2026-06-16.md`
**Status:** Ready for `_working/`

### What Claude Code does

**Create these new files in the repo:**

```
docs/LEARNING_SYSTEM_ROADMAP.md        ← from docs_LEARNING_SYSTEM_ROADMAP_2026-06-16.md
docs/DESIGN_SESSION_PROMPT.md          ← from docs_DESIGN_SESSION_PROMPT_2026-06-16.md
docs/GESPRACHE_ROADMAP.md              ← from docs_GESPRACHE_ROADMAP_2026-06-16.md
docs/decision-records/                 ← new directory, create it
docs/decision-records/README.md        ← one-line description (see below)
docs/decision-records/dr_gesprache_mobile_priority_2026-06-16.md
                                       ← from dr_gesprache_mobile_priority_2026-06-16.md
```

**`docs/decision-records/README.md` content:**
```markdown
# Decision Records

Decision Records capture the reasoning behind significant mini-moi design
decisions — alternatives considered and rejected, constraints that shaped
the decision, assumptions made, known failure modes, and generalizable
principles. They are the reasoning that produced the specs, not the specs
themselves.

See `docs/DESIGN_SESSION_PROMPT.md` for format and usage.
```

**Amend `docs/HANDOFF_PROCESS.md` — add this section:**
```markdown
## Decision Records

For any significant architectural or product decision, produce a Decision
Record alongside the spec. Use `docs/DESIGN_SESSION_PROMPT.md` to
structure the conversation and trigger the DR output.

A DR is worth producing when:
- Multiple alternatives were genuinely considered
- The negative reasoning would be non-obvious from the spec alone
- A constraint shaped the decision that might change later
- A generalizable principle emerged

DR naming: `dr_[topic]_[YYYY-MM-DD].md`
DR location: `docs/decision-records/`
Spec reference: add one line — *Reasoning: see `dr_[name].md`*

When in doubt, produce a short one. 200 words is better than nothing.
```

**Amend `README.md` — add to the docs section:**
```markdown
- [Learning System Roadmap](docs/LEARNING_SYSTEM_ROADMAP.md) — how mini-moi
  builds institutional knowledge over time
- [Gespräche Roadmap](docs/GESPRACHE_ROADMAP.md) — mobile-first voice
  practice vision and build queue
- [Design Session Prompt](docs/DESIGN_SESSION_PROMPT.md) — how design
  conversations are structured and captured
```

### Definition of Done

- All four new `docs/` files exist and render correctly on GitHub
- `docs/decision-records/` directory exists with `README.md` inside
- `docs/decision-records/dr_gesprache_mobile_priority_2026-06-16.md`
  exists and renders correctly
- `docs/HANDOFF_PROCESS.md` includes the Decision Records section
- `README.md` links to all three new docs
- No other files changed in this commit
- Verified: all internal links in the new docs resolve (especially the
  DR reference in `GESPRACHE_ROADMAP.md`)

### Commit

```
Add learning system foundation: roadmap, Gespräche roadmap, design
session prompt, decision-records directory, first DR, HANDOFF_PROCESS
and README amendments.
```

---

## Part 3 — Build Spec B: Gespräche near-term queue

These specs are already written and ready. Claude Code builds them in
priority order. Do not reorder without Robert's instruction.

### Step 1 — Post-analysis dead-end fix (highest priority)

**Spec:** `spec_gesprache_testrun_followup_2026-06-15.md` — **Part C only**

Build Part C first as a standalone commit before touching the consolidated
layout pass. It unblocks the basic loop.

What it does: adds "Neue Sitzung" button in the post-analysis state that
drops `.session-active`, restores the resting layout, clears the transcript
and analysis, keeps the current persona selected. Selecting a different
persona while in session/analysis mode also exits to resting state for
that persona.

**Commit:** `Add Neue Sitzung control — exit session-active back to
resting state after Analysieren (fixes post-analysis dead-end).`

---

### Step 2 — Consolidated layout pass (7 changes + Parts A folded in)

**Specs:**
- `spec_gesprache_consolidated_2026-06-15.md` (primary)
- `spec_gesprache_testrun_followup_2026-06-15.md` Parts A (spacing,
  persona name padding) folded in

Seven changes in one commit:
1. Tab rename — "Mit KI-Persona" / "Mit echtem Mensch" *(may already be
   shipped — confirm before building)*
2. VAD silence window — 1.5s → 2.0s
3. Suppress text flash during session
4. Audio cue on session start ("Sitzung beginnt.")
5. Two-mode layout — `.session-active` class toggle, full right-column
   session area, suggestion pills become turn-senders
6. Feedback box — parchment bg + amber left border (replace dark gray)
7. Collapsible analysis sections — FEEDBACK expanded, STÄRKEN collapsed,
   SCHWÄCHEN expanded, Anki cards collapsed

**Also fold in from Part A:**
- Tighten center column spacing (resting + analysis states)
- Reduce top padding above persona name in detail card

**Commit:** `Gespräche consolidated improvements: two-mode layout, VAD
tuning, audio cue, feedback styling, collapsible analysis, spacing fixes.`

---

### Step 3 — Transcript paste panel

**Spec:** `spec_gesprache_transcript_paste_panel_2026-06-16.md`

Full-width textarea in Nach der Sitzung. Clipboard button fills in one
tap. Status line for feedback states. Pre-populated from KI-Sitzung
transcript or empty for external paste. Analysieren button below.

Three-device verification required:
- [ ] iPhone — Safari
- [ ] iPhone — Chrome
- [ ] Samsung A36 — Chrome

**Commit:** `Add transcript paste panel to Gespräche Nach der Sitzung —
clipboard button, pre-population from KI-Sitzung, mobile-first layout.`

---

### Step 4 — Latency investigation

**Spec:** `spec_gesprache_testrun_followup_2026-06-15.md` — **Part B**

Instrument the turn pipeline first. Capture numbers for one real Maria
session before changing anything:
- `transcribe_ms` — Whisper round trip
- `ai_turn_ms` — `run_chat_turn()` → provider round trip
- `tts_ms` — TTS round trip
- `total_turn_ms` — mic stop → audio playback start

Ship the "denkt nach…" thinking indicator regardless of what the numbers
show — it addresses the "is it even working" problem directly.

Then apply the appropriate lever based on the numbers (likely: route
`run_chat_turn()` to a fast model, keep `run_review()` on stronger model).

**Commits (separate):**
- `Add per-stage timing to Gespräche turn pipeline.`
- `Add persona thinking indicator during AI turn (perceived latency).`
- `Route run_chat_turn() to fast model; keep run_review() on stronger
  model.` *(only if numbers justify)*

---

## Confirmation checklist for Robert before starting

Before Claude Code begins Part 3:

- [ ] Tab rename ("Mit KI-Persona" / "Mit echtem Mensch") — confirm
  whether already shipped or still needed in consolidated pass
- [ ] Persona order — confirm Maria is now first, or still needs the
  reorder from the June 15 session request
- [ ] Part 2 (foundation docs commit) — Robert approves content of all
  five documents before Claude Code commits them. These are public-facing
  GitHub docs.

---

*Handoff · 2026-06-16 · Claude.ai*
