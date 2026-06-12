# Handoff Process — Design to Done
*mini-moi · Guild*
*Created: 2026-06-12 10:15 CDT — Claude.ai*
*Last revised: 2026-06-12 11:07 CDT — Claude.ai*
*Audience: any agent (Claude Code, future Claude.ai sessions, OpenClaw, Grok)*
*Mechanism reference: docs/design/build_discipline_2026-06-12.md*

**Revision history:**
- v0.1 (10:15) — initial process doc, the 7-step loop + blocked/override
- v0.2 (10:17) — added File lifecycle section: done/deferred → archive,
  ties existing Phase 5 archive→trash to the new `status` field; one-time
  catch-up sweep for current `_working/` contents
- v0.3 (10:39) — clarified steps 4-5: the conversational review/questions
  with Claude Code is unchanged and essential. `design_log`/Queue track the
  *outcome* of that conversation (spec_ready → in_build via `start_build.sh`),
  they don't replace it. Distinguished structural (`incomplete`, → Claude.ai)
  vs substantive (→ Claude Code chat directly) questions.
- v0.4 (10:49) — added "What Design/Dev watches — and ignores": the
  extension+prefix filter, extended to cover notifications and memory-log
  entries (not just `design_log` rows). `_working/` is now explicitly safe
  for drafts, screenshots, PDFs, and other non-spec content — zero agent
  footprint unless filename matches the spec-track pattern.
- v0.5 (10:55) — shifted the gate from filename-prefix to content structure
  (DoD section + Commit section + Claude Code framing). Robust to naming
  drift across sessions — a correctly-structured file gets tracked
  regardless of its name; an intended-spec missing required sections
  surfaces as `incomplete` rather than silently going untracked. Filename
  conventions remain documented for human scanning, not load-bearing.
- v0.6 (11:07) — Grok final review, four polish items: moved "What
  Design/Dev watches" to lead the document (most-referenced section first),
  added a minimal spec-track example, clarified the `_working/` catch-up
  sweep as a manual one-time action before Design/Dev's next run, and
  tightened step 5's wording on `start_build.sh` timing.

This is the operational loop now that build discipline is live. Follow this
for any new piece of work in the repo.

---

## What Design/Dev watches — and ignores

**Read this first.** `_working/` is a general staging area, not just build
specs — drafts, screenshots, PDFs for Grok, roadmap notes, exploratory
back-and-forth. Most of what lands there should produce zero agent reaction.

**The filter, applied before any LLM call, log entry, or notification:**

1. **Non-`.md` files** (`.pdf`, `.png`, `.jpg`, etc.) — invisible to
   Design/Dev entirely. Not classified, not logged, no notification.

2. **`.md` files** — one classification call (`grok-4-1-fast-reasoning`)
   determines doc type **from content, not filename**:
   - Reads as a build handoff/spec — has a "Definition of Done" section, a
     "Commit" section, framing addressed to Claude Code for implementation —
     → spec-track: `spec_title` extracted, `design_log` row (`spec_ready`).
   - Reads as *intended* as a spec but missing DoD or Commit (e.g. "For:
     Claude Code" framing present, but no DoD section) → `incomplete`, flagged.
   - Otherwise — design docs, plans, release notes, README drafts, notes,
     exploratory back-and-forth — → not spec-track. No row, no notification,
     no memory-log entry.

**Minimal spec-track example** — this is all it takes to be classified
`spec_ready`:

```markdown
# Spec — Fix the widget
For: Claude Code

(...description...)

## Definition of Done
- [ ] Widget no longer breaks on click

## Commit
\`\`\`bash
git add widget.py && git commit -m "fix: widget click handler"
\`\`\`
```

**Filename conventions are for humans, not the system.** `spec_*`,
`handoff_*`, `build_spec_*`, `build_plan_*` for spec-track files; `design_*`,
`plan_*`, `RELEASE_*` for everything else — useful for visually scanning
`_working/`, documented here as the convention. The classifier doesn't
depend on these: a misnamed file with the right structure still gets tracked
correctly; a correctly-named file missing required sections gets flagged
`incomplete` rather than silently mistracked. This makes the system robust
to naming drift across sessions.

Real deliverables that go to GitHub (README updates, roadmap, final
screenshots) don't need a `_working/` file at all unless you want one
tracked — they're normal conversational asks to Claude Code, committed
directly.

---

## The loop

1. **Spec written** — Claude.ai writes a spec/handoff to `_working/`. Must
   include a "Definition of Done" section and a "Commit" section — these are
   what the completeness check looks for.

2. **Classified automatically** — Design/Dev (`grok-4-1-fast-reasoning`,
   `dev_agent.py`) detects the new/changed file, extracts `spec_title`, checks
   for the DoD section, the Commit section, and verifies any `_working/`
   files it references exist. Writes a `guild.design_log` row:
   `spec_ready` (all checks pass) or `incomplete` (with reason — Design/Dev
   notifies what's missing).

3. **Visible on Queue** — `/guild/build/queue` shows it in "Spec Ready"
   (⚠ tag if `incomplete`). `/guild/build` (Build Log) is the full
   history/reporting view across all statuses.

4. **Robert approves** — opens a chat with Claude Code: *"Review
   `_working/<spec>.md` — any questions before you start?"* Claude Code reads
   it and may ask clarifying questions right there, same as always — the
   system doesn't bypass this. Robert answers in that chat.

   **Two kinds of questions, two destinations:**
   - `incomplete` (structural — missing DoD/Commit, broken file reference):
     flagged automatically via Telegram, usually means back to Claude.ai to
     fix the spec, re-save, re-classify.
   - Substantive questions on a `spec_ready` item ("why this approach,"
     "what about X"): handled directly in the Claude Code chat — no detour
     needed unless the answer changes the design itself.

5. **Build starts** — Claude Code runs `scripts/start_build.sh
   <spec-filename>` once it is ready to begin implementation (still inside
   the same chat thread) — this is the signal "review's done, starting," not
   a separate formal gate. Row flips to `in_build`, transition logged.

6. **Build happens** — Claude Code follows the spec's parts and Definition
   of Done.

7. **Done** — Claude Code confirms DoD complete (visual sign-off where the
   spec calls for it). Design/Dev drafts a build-log entry from the DoD
   checklist + commits since `in_build`. Robert confirms → `done`, entry
   appended to `docs/GUILD_BUILD_LOG.md`. Row stays on the Queue's "Recently
   Done" for 3 days, then Build Log only.

---

## If something needs a decision

Status → `blocked`, `blocked_reason` set — either explicitly (Claude Code or
Claude.ai flags it) or automatically (3-day staleness: a `spec_ready` or
`in_build` item with no progress gets flagged by CoS). Shows in the Queue's
**Blocked** column — check this one first, it's the "needs Robert" list.

---

## Manual override

Any status can be changed by hand via the dropdown on Queue/Build Log cards,
with an optional note. Every change writes to
`guild.design_log_transitions` (`from_status`, `to_status`, `triggered_by`,
`reason`). Use this for: overriding an `incomplete` flag when the spec is
fine as-is, resolving a `blocked` item once the decision is made, or
correcting anything that's drifted from reality.

---

---

## File lifecycle — `_working/` stays current

When a spec's `design_log` status becomes `done` or `deferred`, Design/Dev
moves the corresponding file from `_working/` to `_working/archive/` —
same archive→trash lifecycle Design/Dev already manages (Phase 5), now
triggered by the status transition rather than only on supersession.

`_working/` should contain only what's `spec_ready`, `in_build`,
`incomplete`, or `blocked` — i.e., exactly what's on the Queue. Archived
files proceed through the existing trash-confirmation flow on Design/Dev's
normal cadence — no new mechanism, just a clearer trigger.

**One-time catch-up:** everything currently in `_working/` predates this
policy. **This is a manual, one-time action** — Robert or Claude.ai moves
everything except `handoff_morning_build_2026-06-12.md` (the one active
spec) to `_working/archive/2026-06/`, done before Design/Dev's next run so
it doesn't encounter files mid-move. From there, the existing archive→trash
flow applies whenever Design/Dev next runs it. After this sweep, the
automatic done/deferred→archive logic above takes over for everything going
forward.

---

## What this replaces

Before: build state lived in conversation memory — Claude.ai tracking what's
in flight, writing Definition of Done checklists by hand, confirming "CLOSED"
in chat. Now: `guild.design_log` + `guild.design_log_transitions` are the
source of truth. `/guild/build` and `/guild/build/queue` are where to look.

---

## What's NOT tracked

The `design` phase — conversation, before any file exists in `_working/` —
is not tracked by this system, deliberately. That's exploration and should
stay lightweight. Tracking starts when a spec file lands in `_working/`.

---

*Handoff Process · Guild · 2026-06-12*
