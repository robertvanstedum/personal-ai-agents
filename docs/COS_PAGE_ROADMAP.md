# Roadmap Entry — CoS Interaction Page
*Created: 2026-06-17 — Claude.ai*
*Domain: Guild*
*Status: target — design session pending*
*Priority: Top of Guild domain roadmap*
*Source: docs/COS_PAGE_ROADMAP.md*

---

## What this is

A dedicated CoS (Chief of Staff) interaction page — the strategic layer
above the build queue. Voice-first on mobile. The interface between
Robert and the agent system for things that aren't build specs.

Currently CoS exists as:
- A chat interface on `rvsopenbot` (Telegram)
- Periodic tasks in `cos_context.json`
- Briefing output in the Guild dashboard card

There is no dedicated page. There is no voice interface. There is no
single place to see what CoS is tracking, what it has flagged, what
periodic tasks are pending.

---

## Preliminary approach (to be refined in design session)

### Page placement
Top of Guild domain — above QUEUE and BUILD LOG in the nav.
The CoS page is the strategic entry point; the queue is tactical.

```
← GUILD   CoS   QUEUE   BUILD LOG   ROADMAP
```

### What the page shows
- **Active flags** — what CoS has surfaced that needs Robert's attention
- **Periodic tasks status** — GitHub sanity check, career scout, etc.
  Last run, next run, last finding
- **Briefing** — today's CoS summary (career, build, German, system health)
- **Interaction panel** — voice or text input to CoS directly

### Voice component (primary mobile interface)
Same VAD pipeline as Gespräche — mic → Whisper → intent → CoS response.
On mobile, Robert speaks rather than types. Examples:
- "What's in the build queue?" → CoS reads queue state
- "Flag the guest access spec as blocked" → CoS updates queue
- "Add a note to the German roadmap" → CoS writes to ROADMAP.md
- "What did we decide about the security architecture?" → CoS queries DRs
- "Run the GitHub sanity check" → CoS triggers periodic task

The voice interface makes CoS genuinely useful on the go — commute,
between meetings, walking. Not just a dashboard to open at a desk.

### Text input fallback
When voice isn't appropriate, text input to CoS in the same panel.
Same routing, same responses.

### Response format
CoS responds in the briefing voice — concise, factual, work-product.
Not conversational. Outputs an action confirmation or a fact, not a
paragraph.

---

## Open design questions (for design session)

1. **What can CoS actually action vs. just report?**
   Reading queue state is easy. Updating it requires careful scoping —
   CoS should be able to flag/defer/note, but Robert approves any
   status change that affects what Claude Code builds.

2. **How does the voice session differ from Gespräche?**
   Gespräche is a persona conversation. CoS voice is command/query.
   Different VAD threshold? Different silence window? No TTS response
   needed (CoS shows text, doesn't speak back)?

3. **What's the right scope for a v1 CoS page?**
   Full voice + briefing + task status + interaction panel is a
   significant build. A v1 might be: briefing + task status + text
   input only, with voice added in v1.1.

4. **How does CoS page connect to existing Telegram bot?**
   The Telegram bot (`rvsopenbot`) is the current CoS interface.
   Does the page replace it, complement it, or sync with it?

---

## Learning system connection

The CoS page is the natural home for querying Decision Records:
"What did we decide about X?" routes to the DR index and returns
the relevant decision. This is Phase 1 RAG in action — CoS as the
interface to the project's institutional memory.

---

## Status

- Design session: **pending** — schedule when Gespräche mobile is stable
- Spec: not yet written — produces from design session
- Build: not yet started

---

## Non-build task tracking — design note

**The problem (surfaced 2026-06-17):**
OpenClaw tasks, CoS periodic tasks, and one-off requests have no clean
home. They don't belong in the build queue. They're not roadmap items.
But some of them matter and shouldn't be lost.

**The principle:**
- **Track:** Decisions made with/by CoS that have lasting effect.
  Things that shape how the system behaves. Eventually LoRA-relevant
  CoS reasoning (same DR discipline as design sessions).
- **Don't track:** Adhoc requests, one-off lookups, routine briefing
  interactions. These are conversations, not records.
- **Middle ground:** Occasional to-dos that matter but aren't build specs.

**Interim solution (until CoS page exists):**
A `cos_todos` section in `cos_context.json`. CoS reads it, acts on items,
marks them done. Robert adds via Telegram or direct file edit. No UI
needed yet.

Example structure:
```json
"cos_todos": [
  {
    "id": "t1",
    "note": "Produce local LLM how-to note for Robert",
    "status": "done",
    "added": "2026-06-17"
  }
]
```

**Learned preferences — intentional only:**
CoS never infers preferences from rejected recommendations or conversation
patterns. Background inference can drift and encode wrong patterns silently.

Robert says "note this" → CoS records it. That's the only path.

Examples:
- "Note this — I don't want automated pushes without my confirmation"
- "Note this — rejecting a suggestion means not now, not never"

CoS writes to `cos_preferences` in `cos_context.json`. Before making a
recommendation, CoS checks whether a relevant preference exists and
surfaces it: "I have a note that says X — still want me to suggest Y?"
That's retrieval, not inference. Reading back what Robert told it,
not deciding what it thinks he meant.

```json
"cos_preferences": [
  {
    "learned": "2026-06-17",
    "noted_by": "Robert",
    "note": "No automated git pushes without Robert's confirmation, even low-risk commits"
  }
]
```

**CoS decisions and LoRA:**
When CoS makes a decision that affects system behavior — a routing choice,
a periodic task threshold, a response pattern — that is eventually LoRA
signal, same as design session DRs. The same DR discipline applies:
produce a short DR when CoS reasoning is worth internalizing, not for
every interaction. This is a Phase 2 concern but worth designing for now.

**What this means for the CoS page v1:**
The page should show `cos_todos` — pending and recently done — alongside
the briefing and periodic task status. Completing a todo from the voice
interface ("mark the local LLM task done") is a natural first voice command.

---

*Roadmap Entry · 2026-06-17 · Claude.ai*
*Add to _working/ROADMAP.md under Guild → Agreed targets*
*Commit to docs/COS_PAGE_ROADMAP.md*
