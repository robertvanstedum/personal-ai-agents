# Near-Term Plan — mini-moi
*Created: 2026-06-17 — Claude.ai*
*Agreed by: Robert*
*For: Claude Code, Claude.ai, Grok — open this at the start of any session*

---

## Current state

Build queue is clean — 0 spec ready, 0 in build, 0 incomplete.
All recent work committed. Starting fresh from the Roadmap.

Local LLM: Ollama running, gemma3:1b stable, Open WebUI at
http://localhost:8080. Robert using it. Next: wire into design sessions
as junior partner per Learning System Phase 0.

---

## Agreed priority sequence

### Step 1 — German batch (next build session)
*Spec and hand to Claude Code — one session, one commit*

Four small independent items, no design session needed:

| Item | What it does |
|------|-------------|
| T1-B — Safe area + font audit | `env(safe-area-inset-bottom)` on German bottom tab bar. All inputs ≥ 16px (prevents Safari auto-zoom). One pass, all German pages. |
| T2-C — Post-session summary card | Compact card above Nach der Sitzung: turns, persona, duration. No LLM call — from session state only. |
| T2-A — iOS Share Sheet | `navigator.share({ text: transcript })` after session. Native share sheet. Gate with `if (navigator.share)`. |
| T2-B — Schreiben save toast | 1.5s "Gespeichert ✓" after POST /api/write-save. |

Order within the batch: T1-B first (foundation fix), then T2-C, T2-A, T2-B.
All four verified on iPhone Safari, iPhone Chrome, Samsung A36 Chrome.

**Reference:** `docs/GESPRACHE_FORWARD_SPEC.md` — testing checklist inside.

---

### Step 2 — CoS page design session
*Design session with Claude.ai — produces design doc and spec*

The CoS interaction page is the highest-value new build. Voice-first
on mobile. The interface between Robert and the agent system for things
that aren't build specs.

Do this while Gespräche is fresh — several voice commands are
Gespräche-adjacent ("summarize my last session," "what did I get wrong
this week," "add a scene to Maria").

**What the design session covers:**
- What CoS can action vs. just report (v1 scope)
- Voice vs. text input — VAD threshold, silence window, no TTS needed?
- How `cos_todos` and `cos_preferences` surface in the page
- "Summarize our discussion" / "save it" flow in the UI
- Connection to existing Telegram bot — complement or replace?
- v1 scope decision: briefing + task status + text input only,
  voice in v1.1?

**Reference:** `docs/COS_PAGE_ROADMAP.md` — preliminary approach and
open design questions inside.

**Gate:** Run the design session, produce a spec, hand to Claude Code.

---

### Step 3 — CoS page build
*Claude Code builds from spec produced in Step 2*

No detail here yet — depends on Step 2 output.
Expected: voice input panel, CoS briefing, `cos_todos` list,
`cos_preferences` visible, periodic task status.

---

### Step 4 — Gespräche Phase 1 (automatic transcript handoff)
*After 2 weeks of daily use data from the current flow*

Remove manual Analysieren tap. Transcript posts automatically at session
end, review runs in background, results waiting when Nach der Sitzung opens.

**Gate:** 2 weeks of stable daily use with current flow. Don't build
before the gate — need real usage data to confirm the loop is solid.
Design session needed before spec.

---

## Parallel track — Code Review Phase 0

Runs alongside AWS Phase 0, not blocking it. Starts when
containerization begins. Three review sessions (Portal → German →
Curator), one findings report per session.

**Trigger:** AWS Phase 0 kickoff handoff sent to Claude Code.
**Spec:** `spec_code_review_phase0_2026-06-19.md`
**Plan:** `docs/CODE_REVIEW_PLAN.md`

Each review session is one handoff to Grok or Claude Code with
specific file paths and the grep commands from the spec. Findings
reviewed by Robert, fixes applied by Claude Code, report committed.

---

## Parked — not near-term

These are valid but deliberately not sequenced now. Revisit when the
above steps are done or circumstances change:

| Item | Why parked |
|------|-----------|
| Security architecture split | Preview working well, no urgency. Revisit when actively sharing live portal. |
| Guest access + CoS nudge | Same — low urgency until security is done. |
| Career focus editor | Low priority, no interview dependency right now. |
| Voice command routing | Needs 2-4 weeks daily use data first. |
| Decisions view UI | Needs DR inventory (~15 DRs) before worth building. |
| PWA wrapper | After mobile loop is fully polished. |

---

## Roadmap clarifications (from 2026-06-17 review)

- **Gespräche toggle (pill):** Dropped. Tab rename covers the intent.
- **Roadmap view maturity:** Mark as done — renders correctly.
- **Learning System Phase 0:** Habit only, no build component yet.
  In progress = DR habit establishing, local LLM evaluation starting.
- **Mein Deutsch landing blurb:** Spec exists
  (`spec_mein_deutsch_blurb_release_update_2026-06-15.md`).
  Add to Guild queue as spec ready.
- **CoS design session gate:** Schedule after Step 1 ships.

---

## Open questions to answer in Step 2 design session

From `docs/GESPRACHE_FORWARD_SPEC.md` — test these in daily use
before the CoS design session:

- Does text-only fallback (E3) need to be built? Test Web Speech API
  reliability on iPhone Safari.
- Is persona card preview (E5) still needed after the mobile scroll fix?
- Does Letzte Sitzungen feel sufficient or does "Continue" card (E2)
  add real value?
- What are the actual latency numbers from the instrumented pipeline?

---

## For the next Claude.ai session

Open with this document. Current priorities in order:
1. Spec the German batch (Step 1)
2. Schedule CoS design session (Step 2)

Nothing else needs attention before Step 1 is built.

---

*Near-Term Plan · 2026-06-17 · Claude.ai*
*Update this document when steps complete or priorities shift*
*File: `_working/NEAR_TERM_PLAN.md`*
