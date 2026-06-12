# Plan — Six-Week Focus (Morning Read)
*mini-moi · Guild*
*Authored: 2026-06-11 20:45 CDT (2026-06-12 01:45 UTC) — Claude.ai + Robert*
*Supersedes the "tomorrow morning" section of plan_cos_soul_v0.1_2026-06-11.md*
*Status: planning only — no build started from this doc*

---

## The frame

Everything below serves one goal: protect Robert's attention for the job search
over the next six weeks, while using mini-moi itself as a working proof-of-concept
for a transferable pattern — what AI-augmented ways of working could look like
inside an organization, with Robert as the architect and validator.

Two things follow from this:

1. **Stay tactical.** The POC's value comes from being real and used, not from
   being generalized. Don't build for "any team" — build for Robert's actual
   six weeks, then write the translation separately.

2. **Career search is the priority activity; mini-moi is the supplement.**
   Guild's job search functionality supports the search. It does not replace
   the work of searching, and it should never compete with it for attention.

---

## Career — what's left, then stop

**The reframe (tonight):** Robert's own applications (already out — T-Mobile and
others) are the primary content of the pipeline. The assisted search (Loop A) is
a supplementary discovery feed, not the driver. The tool needs to reflect this:

### Remaining build (small, before "stop")

1. **Manual entry path** — a simple form to add a position directly: title,
   company, geo, type, url, status (default: applied, since these are
   already-sent applications). This is now the *primary* entry path, not a
   gap-fill.

2. **`source` field** — `manual` vs `loop_a`. Distinguishes Robert's own
   pipeline from assisted-search suggestions. Both live in the same table;
   visually distinct so Suggested (loop_a) never competes for attention with
   Active Pipeline (manual, starred, in motion).

3. **Open and use the Active Pipeline board** with Robert's real current
   applications — T-Mobile and others go in now, starred. Confirm close-reason
   flow end-to-end with real data.

### Then stop

No further Career feature work after items 1-3 land and are confirmed working
with real positions. Loop A keeps running as background supplement. Robert's
actual job search activity — outside the tool — is where the next six weeks
of effort goes.

---

## Build — the near-term Guild priority

**Handoff automation** between design (Claude.ai) and build (Claude Code) is
the priority. Concretely: catch the failure mode we hit twice this session —
a spec exists in Claude.ai's output but isn't committed to `_working/`, so
Claude Code can't find it and the handoff stalls.

This is not just hygiene. It's the most demonstrable artifact in the whole
project for the "AI-augmented delivery process" story — design agent, build
agent, review agent (Challenger), process agent (CoS), with Robert as the
human decision point at every consequential step. Making this loop tight and
visible *is* the proof, not a description of the proof.

**First concrete target (scope tomorrow):** something that checks "is
everything referenced in `docs/GUILD.md` or recent handoffs actually present
in `_working/`" — even a simple script Robert or OpenClaw runs is enough to
start. Doesn't need to be automatic yet; needs to catch the gap before it
becomes a blocked build.

---

## CoS — three mandates, one soul

All three mandates exist to reduce what needs Robert's attention:

1. **Operations** — already running. Health, disk, Tier 1-4 escalation. Keep
   it solid; no new work needed unless something breaks.

2. **Build discipline** — CoS *consumes* the Design/Dev agent's
   `guild.design_log` (does not duplicate its watching function). Surfaces
   in the daily briefing: what's in flight, what's been waiting too long,
   what handoffs are incomplete. CoS as process enforcer sitting on top of
   Design/Dev's record-keeping.

3. **Agent intelligence** — weekly cadence, forward-looking. Not a generic
   news roundup: the brief should check mini-moi's actual architectural
   choices (model-agnostic design, selective persona application via
   `cos_soul.md`, the Challenger pattern) against what's emerging in the
   field, and report whether each choice is confirmed, challenged, or
   needs revisiting. Tonight's persona-prompting research is the template
   for what "good" looks like here.

**Soul wiring (5 min job, do this first):** `cos_soul.md` gets read into the
`/chat` system prompt. One small code change. Then move to Build.

---

## Portfolio — the translation document

New deliverable, not previously planned: a short document (working title
`docs/PATTERN.md`, or a section on minimoi.ai) that translates mini-moi's
domains into organizational terms. Pure writing — no new code.

| mini-moi domain | Organizational equivalent |
|---|---|
| Curator | Market & competitive intelligence — scored daily briefing against team priorities |
| Mein Deutsch pipeline | Skill-development loop — friction-earned mastery vs passive training |
| Guild pipeline (`pipeline.items`, context field) | One pattern for sales opportunities, project intake, hiring — context is a field, not a rebuild |
| CoS | Team chief-of-staff — triage, daily briefing, cross-functional pattern recognition |
| Operations / Design-Dev / Challenger | AI-augmented delivery process — design, build, review, and process agents with human checkpoints |

This table is the seed. It turns "personal AI project" into "working model
of organizational transformation, validated on myself, here's the blueprint."
Directly extends the existing resume thesis (breadth + judgment matter more
as technician work gets automated).

**Timing:** after Build's handoff automation has something concrete to point
to — the translation doc is stronger with a real example of the delivery
process working, not just a description of intent.

---

## Timeline

**By ~July 1:** German v1.0, Curator v1.2, Guild (Career two-page + soul +
build discipline) all in production together.

**July — one month:** production use across all three domains. Learning and
incremental changes only — no new scope. This is also the window where the
job search is most active and most needs mini-moi to stay out of the way.

**Post-Aug 1:** revisit deferred items — Mein Deutsch v1.1 (Gespräche toggle,
Lesen writing drill), the bookshelved CoS identity work (principal profile,
composite character), translation doc refinement, roadmap for what's next.

---

## Morning order of operations

1. Wire `cos_soul.md` into CoS `/chat` (5 min)
2. Career: manual entry form + `source` field — small build
3. Robert adds real current applications (T-Mobile, others) to the pipeline,
   stars priorities, opens Active Pipeline board
4. Build: scope the handoff-gap-check (first concrete automation target)
5. Everything else from this doc happens across the week, not all tomorrow

---

*Six-week focus plan · Guild · 2026-06-11 (committed for 2026-06-12 morning read)*
