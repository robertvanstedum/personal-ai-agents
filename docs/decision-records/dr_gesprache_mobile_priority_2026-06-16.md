# Decision Record — Gespräche Mobile Priority & Learning System Foundation
*2026-06-16 · Claude.ai · mini-moi*

---

## Decision

Gespräche on mobile is the primary investment priority for the German domain.
Reading and writing are present and useful but not the mobile driver. The
Gespräche mobile experience — voice conversation, persona practice, transcript
analysis, drilling — is where focused improvement effort goes. In parallel,
mini-moi begins a structured approach to institutional knowledge: design
session prompts produce Decision Records, a local LLM is introduced as a
junior partner evaluated in the design flow, and RAG and LoRA are sequenced
as the signal accumulates to justify them. These two directions — Gespräche
mobile and the learning system — are connected, not parallel: Gespräche
sessions generate the signal the learning system needs.

---

## Context

Robert tested Gespräche on iPhone after today's mobile audit. The experience
was good enough to recognize as a genuine showcase — voice conversation with
KI-Personas, real-time feedback, the portfolio piece you could pull up in an
interview. But several gaps were blocking the full loop: no way back to start
after Analysieren, dead vertical space, slow response with no indicator, no
path to paste an external transcript back in.

Separately, a discussion about what mini-moi could become over time surfaced
the learning system direction: local LLM as institutional memory, RAG for
current-state knowledge, LoRA for internalized design philosophy and German
error patterns. The question was how to sequence this without building ahead
of the signal.

Contract ends August 3, 2026 — approximately 7 weeks. Interview demos are
active. Both factors push toward making the mobile experience interview-ready
now, and toward starting the learning system foundation with zero-cost
workflow changes rather than infrastructure builds.

---

## Alternatives considered

### Focus mobile investment equally across all German tabs

**What it was:** Treat Lesen, Schreiben, Wörter, and Gespräche as equal
mobile priorities and improve all of them in parallel.

**Why it was attractive:** The mobile audit found issues across all tabs.
A complete fix feels cleaner than a partial one.

**Rejected because:** Language practice on a phone is fundamentally a
voice and conversation activity. Reading on a small screen is worse than
reading on a larger one. Writing practice is deliberate and unhurried —
not a mobile-native behavior. Gespräche is the only tab where the phone
is actually the better device. Equal investment would dilute the effort
that makes the most impact. Lesen and Schreiben benefit from general
mobile improvements (font size, touch targets, safe areas) without needing
dedicated mobile-specific features.

---

### Build the learning system infrastructure first, then use it

**What it was:** Spec and build the RAG layer and LoRA pipeline before
introducing the local LLM into the design flow.

**Why it was attractive:** Clean sequencing — infrastructure first, then
use. Avoids using an under-equipped tool.

**Rejected because:** The evaluation of the local LLM in the design flow
IS the preparation for the infrastructure. You don't know what the RAG
layer needs to serve, or what the LoRA training data should look like,
until you've used the local LLM in real design conversations and seen
where it falls short. Waiting for perfect infrastructure means never
starting. The cost of starting now is zero — paste the design session
prompt, consult the local LLM, note the response quality. That data shapes
Phase 1.

> [FLAG: principle — start using the local LLM now as a junior partner
> to evaluate quality in the design flow, not after the infrastructure
> is perfect. The evaluation IS the preparation.]

---

### Use LoRA or RAG alone, not both

**What it was:** Choose one approach for local LLM knowledge — either
fine-tune it on project data (LoRA) or give it retrieval access to current
files (RAG).

**Why it was attractive:** Simpler architecture, one system to maintain.

**Rejected because:** They solve different problems and operate at different
layers. RAG is for lookup — specific facts in specific files, current
codebase state, current spec queue. LoRA is for internalized reasoning —
design philosophy, working conventions, the *why* behind decisions. A model
with only RAG retrieves your files correctly but reasons about them like a
stranger. A model with only LoRA has internalized your intent but is working
from a snapshot that goes stale. Both are needed. They are complementary,
not competing.

> [FLAG: principle — the local LLM is the institutional memory; frontier
> models are the consultants. Neither replaces the other.]

---

### Capture only final decisions, not the reasoning that produced them

**What it was:** Continue the current approach — specs capture what to
build, session summaries capture what was built. Reasoning lives only in
conversation history.

**Why it was attractive:** Less overhead. The specs are already working.
Adding another document type adds process weight.

**Rejected because:** The negative reasoning — what was considered and
not built, what failed and why, what constraints shaped a decision — is
not in the specs and not reliably in the session summaries. It exists only
in conversation history, which is not searchable, not indexable for RAG,
and not usable as LoRA training data. The reasoning record is more
valuable training signal than the spec because it contains the full
decision surface, not just the winning path. A model trained only on
specs learns what was built. A model trained on decision records learns
how to think about what to build.

> [FLAG: principle — the negative reasoning is as important as the
> positive. What was rejected and why is not a footnote; it is the
> primary signal for reasoning about future decisions.]

---

### Replace frontier models with local LLM as it improves

**What it was:** As the local model gets better through LoRA training and
RAG, gradually replace Claude and Grok with it for design and review work.

**Why it was attractive:** Cost reduction, no usage limits, full local
control, model-agnostic principle.

**Deferred / partially rejected because:** Frontier models improve faster
than local ones. Claude and Grok will always bring something the local
model doesn't — current capability, outside perspective, broad training
across domains. The local LLM's value is institutional memory and
current-state knowledge about *this project specifically*. Frontier models'
value is best practices, novel problems, and outside perspective. These
roles are complementary. The right end state is: local LLM as daily
driver for project-specific questions, frontier models as consultants for
complex architectural decisions and quality checks. Not replacement —
specialization.

> [FLAG: deferral — full local-first operation is Phase 3, ~6 months.
> The prerequisite is Phase 1 (RAG) and Phase 2 (LoRA) being stable
> and in daily use.]

---

### Automatic transcript handoff now

**What it was:** Remove the manual Analysieren tap immediately — transcript
posts automatically at session end, analysis runs in the background.

**Why it was attractive:** Tightens the practice loop, reduces friction,
natural next step after the basic flow works.

**Deferred because:** The basic loop isn't fully working yet. Part C
(no exit from analysis), the consolidated layout pass, and the transcript
paste panel are all ahead of this. Building automatic handoff on top of
a broken loop adds complexity before the foundation is solid. Sequence:
fix the dead-end and layout first, confirm the loop works in daily use,
then automate the handoff. Two weeks of stable daily use before adding
the automatic step.

---

### Voice commands now

**What it was:** Add voice command intent routing to Gespräche immediately
— "Neue Persona erstellen," "Schwerpunkt ändern," "Wiederhole" — as
a natural extension of the VAD pipeline already in place.

**Why it was attractive:** Mobile-native, removes menu navigation, directly
extends what's already built. The VAD → Whisper pipeline is there.

**Deferred because:** The existing conversation flow has unresolved issues
(dead-end, latency, layout). Adding a second routing mode before the first
mode is stable and in daily use is the wrong sequence. The intent
classifier adds surface area to a system that needs to be reliable first.
Also: the command vocabulary should be shaped by what you actually reach
for in practice sessions — build from observed need, not anticipated need.
Two to four weeks of daily mobile Gespräche use will surface the commands
worth building.

> [FLAG: principle — build voice commands from observed need in daily
> use, not from anticipated need in design sessions.]

---

## Constraints that shaped this

- **Contract ends August 3, 2026 (~7 weeks):** Interview demos are active.
  Mobile experience needs to be interview-ready in the near term, not
  eventually. This accelerates the mobile priority and the foundational
  learning system steps (which cost nothing to start).

- **Signal must precede infrastructure:** The Neo4j/Postgres discipline
  applies here. RAG and LoRA are only worth building when there is enough
  accumulated signal (session transcripts, decision records, design
  conversations) to make them high-signal rather than high-noise. Phase 0
  generates that signal. Phase 1 and 2 use it.

- **Mac Mini compute:** Full fine-tuning is not practical on the current
  hardware. LoRA (adapter-only training) is. This constrains the training
  approach but not the outcome — LoRA is actually the better fit for a
  project that changes frequently.

- **Daily-use system:** Gespräche is used daily. Changes that add
  operational risk to a working daily-use system require higher justification
  than changes to a less-used feature. Automatic handoff and voice commands
  are deferred partly for this reason.

---

## Assumptions made

- **Local model quality improves sufficiently in 6 months** for conversation
  practice at a useful level. If base model capability doesn't advance as
  expected, Phase 3 timeline shifts. The RAG and LoRA work still has value
  regardless — it makes whatever model you're using better at your specific
  project.

- **Daily Gespräche use continues** at the current pace. The LoRA training
  timeline assumes 3-6 months of session accumulation. If usage drops, the
  signal accumulates more slowly.

- **The design session prompt is lightweight enough to use consistently.**
  If producing Decision Records feels like overhead rather than value, the
  discipline won't stick. The format should be adjusted to reduce friction
  before abandoning the practice.

---

## Known failure modes

- **Global CSS overrides in shared components** interact badly with
  domain-specific mobile rules. Today's German blank was caused by
  `proxy.py` injecting `nav{top:38px!important;}` globally, which blocked
  the German mobile bottom tab bar. Any future shared CSS in `proxy.py`
  or injected style tags must be scoped to desktop with
  `@media (min-width:769px)` or equivalent.

- **Two-mode layout without an exit path** creates a dead-end that blocks
  the basic loop. The `.session-active` pattern (enter session, exit to
  resting) must always define both entry and exit. The Gespräche
  post-analysis dead-end is the specific failure of this pattern.

- **Latency without a visible indicator** causes users to think the system
  has stopped. The "nearly restarted" experience from today's test is the
  failure mode. Ship the thinking indicator regardless of which latency
  lever the numbers point to.

---

## Principles this decision reflects

- "The phone is the natural device for language practice. The practice
  environment should match the environment where the skill is used."

- "Start using the local LLM now as a junior partner. The evaluation is
  the preparation."

- "The local LLM is the institutional memory; frontier models are the
  consultants. Neither replaces the other."

- "The negative reasoning is as important as the positive. What was
  rejected and why is the primary training signal."

- "Don't build the infrastructure before the signal that justifies it
  exists. Phase 0 generates the signal; Phase 1 uses it."

- "Build voice commands from observed need in daily use, not from
  anticipated need in design sessions."

- "Don't add operational risk to a working daily-use system for
  incremental gains. Measure first."

- "Don't genericize the label until the mechanism is also generic."

- "Global CSS overrides in shared components must be scoped to desktop.
  Mobile rules in domain CSS must not be blocked by shared overrides."

---

## What this is not

- A decision to deprioritize Curator or Guild. Those domains have their
  own roadmaps. This decision is about where mobile investment goes within
  the German domain.

- A decision to stop using Claude or Grok. The local LLM is an addition
  to the team, not a replacement. Frontier models remain the primary for
  complex architecture and quality checks.

- A decision to build the learning system now. Phase 0 is workflow changes
  only — no new infrastructure. The decision is to start accumulating the
  signal, not to build the infrastructure that will use it.

- A commitment to the 6-month timeline as fixed. Local model capability
  is improving faster than expected in some areas and slower in others.
  Review at each phase transition.

---

## Flags from this session

- [FLAG: principle — start using the local LLM now as a junior partner.
  The evaluation IS the preparation.] — worth capturing because the
  instinct to wait for perfect infrastructure before using an imperfect
  tool is the failure mode that delays everything.

- [FLAG: principle — the local LLM is the institutional memory; frontier
  models are the consultants.] — the long-term employee / consultant
  analogy. Worth having explicit so it doesn't get re-litigated as the
  local model improves.

- [FLAG: principle — the negative reasoning is as important as the
  positive.] — the original observation that sparked the decision record
  discipline. The specs capture the winning path; the DR captures the
  full decision surface.

- [FLAG: deferral — full local-first operation is Phase 3, ~6 months.]
  — not a rejection of local-first, a sequencing decision. Revisit when
  Phase 1 and 2 are stable.

- [FLAG: principle — build voice commands from observed need, not
  anticipated need.] — applies broadly, not just to voice commands.

---

## Open questions

- What is the right LoRA update cadence — time-based (every 6-8 weeks)
  or milestone-based (at major project phases)? To be determined when
  Phase 2 is closer.

- Which local model (via Ollama) is the right base for the first LoRA
  adapter? Depends on what's available and capable at Phase 2 time.
  Don't decide now.

- Does the Design Session Prompt format need adjustment after the first
  5 uses? Treat the format as provisional for the first month. Adjust
  based on what's actually captured vs. what would have been useful.

---

*Decision Record · 2026-06-16 · Claude.ai*
*File: `docs/decision-records/dr_gesprache_mobile_priority_2026-06-16.md`*
