# Build Queue Triage — Incomplete Items
*Created: 2026-06-17 — Claude Code*
*Audience: Claude.ai (OpenClaw)*
*Task: Review 6 incomplete items, recommend Roadmap vs. keep in queue*

---

## Context

The Guild build queue (`guild.design_log`) tracks specs through:
`spec_ready → in_build → done` (or `deferred` / `incomplete`).

Robert's observation: most of the 6 incomplete items look like design
discussion or roadmap entries, not build-ready specs. He wants Claude.ai's
opinion on whether they belong in the queue or should move to the Roadmap
and be closed as deferred here.

The Roadmap lives at `_working/ROADMAP.md`. Items there are agreed targets
that need a design session + spec before entering the build queue.

---

## The 6 Incomplete Items

### ID 67 — Register Three Items
**File:** `_working/handoff_register_three_items_2026-06-17.md`
**Summary:** Meta-handoff asking Claude Code to commit two docs
(`GESPRACHE_FORWARD_SPEC.md`, `COS_PAGE_ROADMAP.md`) to `docs/` and update
the roadmap. Both docs now exist in `docs/` (IDs 71 and 72 below), so
the file actions appear complete.

**Robert's read:** Probably done. Should this be deferred?

---

### ID 68 — Gespräche Forward Spec (working copy)
**File:** `_working/docs_GESPRACHE_FORWARD_SPEC_2026-06-17.md`
**Summary:** Working copy of the Gespräche forward spec. The canonical
version was committed to `docs/GESPRACHE_FORWARD_SPEC.md` (ID 71).

**Robert's read:** Duplicate of 71. Should be deferred.

---

### ID 69 — Local LLM Interaction Guide
**File:** `_working/task_openclaw_local_llm_guide_2026-06-17.md`
**Summary:** OpenClaw task — check what's installed (Ollama, models,
web UI), write a short "how to chat with your local LLM" guide for Robert,
surface it via Telegram or CoS briefing. Not a build spec — it's an
OpenClaw research/briefing task.

**Robert's read:** This is for OpenClaw, not the build queue. Should
probably be deferred here and handled as an OpenClaw task directly.

---

### ID 70 — CoS Interaction Page (working copy)
**File:** `_working/docs_COS_PAGE_ROADMAP_2026-06-17.md`
**Summary:** Working copy of the CoS page roadmap entry. The canonical
version was committed to `docs/COS_PAGE_ROADMAP.md` (ID 72).

**Robert's read:** Duplicate of 72. Should be deferred.

---

### ID 71 — Gespräche Forward Spec (canonical)
**File:** `docs/GESPRACHE_FORWARD_SPEC.md`
**Summary:** Forward spec for Gespräche mobile showcase. Documents what's
shipped, outlines next-tier features (T2-A iOS Share Sheet, T2-B Schreiben
toast, etc.), open design questions, and testing checklist. These next-tier
items need design sessions before specs can be written for the build queue.

**Robert's read:** This is a roadmap/design doc, not a build spec. Should
be on the Roadmap as an agreed target, not sitting in the queue as incomplete.

---

### ID 72 — CoS Interaction Page (canonical)
**File:** `docs/COS_PAGE_ROADMAP.md`
**Summary:** Roadmap entry for a dedicated CoS interaction page — voice-first
on mobile, shows active flags, periodic task status, briefing, and direct
CoS interaction panel (voice + text). Design session pending. No spec yet.

**Robert's read:** Pure roadmap item — agreed target, design session
needed before it enters the build queue. Should move to Roadmap and be
deferred here.

---

## Questions for Claude.ai

1. **IDs 67, 68, 70** — Do you agree these are done/duplicates and should
   be deferred? Any reason to keep them in the queue?

2. **ID 69** — Is the Local LLM Guide something you want to pick up now as
   an OpenClaw task, or defer it? Robert hasn't asked about it recently.

3. **IDs 71 and 72** — These are the substantive ones. Do you agree they
   belong on the Roadmap rather than the build queue? If yes, what's the
   right Roadmap placement:
   - Gespräche forward spec → German domain, agreed targets, next design session
   - CoS page → Guild domain, agreed targets, top priority (above queue/build log)

4. **Anything missing?** Are there items that should be in the build queue
   that aren't? Robert has mentioned Gespräche toggle (KI-Personas /
   Konversation pill) as next — that's not in the queue yet.

---

## Recommended actions (for Claude.ai to confirm or revise)

| ID | Item | Recommended action |
|----|------|--------------------|
| 67 | Register Three Items | Defer — actions already done |
| 68 | Gespräche Forward Spec (working) | Defer — duplicate of 71 |
| 69 | Local LLM Guide | Defer from queue — OpenClaw picks up separately if Robert wants it |
| 70 | CoS Page (working) | Defer — duplicate of 72 |
| 71 | Gespräche Forward Spec (canonical) | Move to Roadmap → German domain; defer from queue |
| 72 | CoS Interaction Page (canonical) | Move to Roadmap → Guild domain top; defer from queue |

If Claude.ai agrees, Claude Code will execute: update `_working/ROADMAP.md`
for 71 and 72, then mark all 6 as deferred in `guild.design_log`.
