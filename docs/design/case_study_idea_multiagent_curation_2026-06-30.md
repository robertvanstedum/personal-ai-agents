# Case Study Idea: Multi-Agent, Multi-Model Curation vs. Sequential Pipeline
*Created: 2026-06-30 · Claude.ai (design/strategy)*

**Status:** Idea-stage. Purpose and approach only. NOT a build plan, NOT a spec.
No Definition of Done, no round counts, no model lock-in, no cost figures. This
gets another round or two of review before any POC plan is written.

**Standalone:** This is independent of minimoi's production code, run as a
separate script/harness against real or sampled Curator data. Not wired into
the live pipeline.

---

## Origin

Sparked by reviewing AutoResearch (Karpathy, March 2026) — an agent loop pattern
of propose → test → keep/revert, run unsupervised, reviewed after the fact. The
question: does that pattern, applied to Curator's curation decision, produce
something measurably better than the current sequential pipeline — and is the
*disagreement itself* informative, separate from whether the final list improves?

This also serves as a stress test of a broader question raised elsewhere in
current design thinking: when does multi-agent/multi-model architecture earn its
cost over a simpler deterministic pipeline, and how would we know?

---

## Current state (the baseline being tested against)

Curator's existing pipeline is sequential and deterministic: search → filter →
score → curated list. Single pass. No stage ever revisits a decision once made.
~700 articles scored daily via Grok; top items go to web portal and Telegram.

---

## The idea

Replace (experimentally, not in production) the filter/score stage with multiple
models holding the same goal but reasoning independently, then — if warranted —
arguing with each other over the same evidence, and converging (or not) on a
final list. Compare that final list against the current pipeline's list, on the
same day's real candidate set, judged blind by Robert.

### Why models, not just prompts
Three instances of one model with different system prompts tests whether
prompt-induced perspective produces real disagreement — useful, but the
disagreement could be purely stylistic. Three different model families (e.g.
Claude, GPT, Grok) reasoning on the *identical* prompt and *identical* 700
articles tests something stronger: do models trained differently actually see
different things in the same evidence. That is the more interesting and more
architecturally relevant question, and it doubles as a live rehearsal of
model-agnosticism — the harness has to treat each model as interchangeable.

---

## What this is actually testing (the real hypothesis)

A fixed pipeline can be wrong in ways invisible to itself — if scoring weights
are subtly off, every article fails the same way, silently, every day. The
hope with multi-model debate is that disagreement *surfaces* those blind spots:
if one model is biased toward mainstream sources, a model weighted toward
variety will say so, on the record, every run.

**What's being purchased is not "a better list" in the abstract — it is visible
disagreement as a diagnostic signal.**

The failure mode that would make this NOT worth pursuing: if the models
converge to nearly the same list every time, the debate is theater — expensive
calls producing what one good prompt already gave you. That convergence
outcome is itself a valid and useful finding, not a wasted run.

---

## Approach — three gates, each can stop the experiment early

Structured so the experiment can fail cheaply rather than running to
completion regardless of signal.

### Gate 1 — Independent parallel runs (cheap)
Same day's ~700 candidate articles. Same prompt/goal (current Curator scoring
criteria, stated once). Each model produces its own curated list independently
— no cross-talk.

**Measure — quantitative:** overlap between the lists (e.g. Jaccard
similarity — intersection over union).

**Measure — qualitative (not just the overlap number):** Jaccard alone can
hide as much as it reveals — two lists can score high overlap while still
disagreeing meaningfully on *why* an article was chosen, and two lists with
low overlap might disagree for one simple, identifiable reason rather than
many. So alongside the similarity score, tag *what kind* of disagreement
shows up where lists diverge: depth piece vs. trending piece, mainstream vs.
niche source, or a pattern like "Model A systematically penalizes mainstream
sources while Model B rewards them." The qualitative tag is what actually
tells you whether a divergence is a real, fixable blind spot or just noise —
the number alone can't.

**Decision point:** high overlap *and* no notable qualitative pattern → little
for debate to add; the pipeline's scoring logic is probably already adequate;
stop here, cheaply. Meaningful divergence, or a high-overlap score that still
hides a real qualitative pattern → proceed to Gate 2.

### Gate 2 — Cross-review with an explicit agreement task (moderate cost)
Each model sees the *other models' actual lists* — grounded reaction, not
abstract debate: "here's what model A's list says about what matters, here's
what it's missing, here's what I'd push back on." But the task is concrete and
bounded, not open-ended discussion: **produce one agreed top-20**, either by
genuine agreement or by explicit, argued compromise.

**Capture both the resulting list AND how they got there** — the path is as
diagnostic as the destination. Three sub-outcomes to watch for and tag
explicitly:

- **Clean consensus** — the lists already overlapped enough that agreement was
  easy. Worth cross-checking against the Gate 1 overlap score: if Gate 1
  showed low overlap but Gate 2 still converges easily, that's notable on its
  own — models may pick different articles but agree on what's good once they
  see each other's reasoning.
- **Dominance** — one model's list wins out and the others concede. Worth
  capturing *why* in the transcript: better arguments, or the others simply
  not pushing back. That's a model-behavior finding (persuasiveness vs.
  correctness) independent of whether the dominant list was actually better.
- **Negotiated compromise** — a genuine blend with visible trades ("I'll drop
  this one if you drop that one"). The most interesting outcome, but also the
  one to scrutinize hardest: are the trades principled (tied to a stated,
  defensible criterion) or just averaging-to-please? **A forced-consensus
  list can end up worse than any single model's original list** if
  negotiation degenerates into splitting the difference rather than genuine
  persuasion. This is a known failure mode of consensus processes generally,
  and this case study should be designed to catch it, not just hope for
  convergence.

The full transcript is diagnostic on its own, independent of whether
convergence happens — it shows *what kind of thing* models disagree about,
which is the blind-spot-surfacing value this whole study is chasing.

### Gate 3 — Two separate readouts, not one verdict (the real test)
Gate 3 asks two different questions that can disagree with each other, and
both should be captured rather than collapsed into a single judgment:

1. **Diagnostic question:** did the Gate 2 process surface anything specific
   and articulable about the pipeline's current scoring logic — a blind spot,
   a bias, a category it systematically over- or under-weights — independent
   of which list Robert ends up preferring?
2. **Preference question:** presented blind (method not labeled), which list
   does Robert actually prefer today — the Gate 2 output or the current
   pipeline's actual list, same day, same 700 articles?

These can diverge: the process could surface a real, useful blind spot while
Robert still prefers the simpler pipeline's list that day, for reasons
unrelated to the blind spot found. That divergence is itself informative, not
a contradiction to resolve. Treating Gate 3 as one collapsed verdict risks
marking a genuinely informative experiment a "failure" just because the final
list wasn't preferred.

---

## The multi-day question (raised by Robert's "scroll forward" framing)

Robert wants to start with one day, then run forward across subsequent days —
*and* have previous converged lists/transcripts available as input to future
runs. This is not just "repeat the experiment N times for consistency." Giving
the loop access to its own history raises a distinct fourth question, separate
from the three gates:

**Does the loop's behavior change when it has memory of its own past output?**
Possibilities: it stabilizes usefully (agents converge faster, build on prior
resolved disagreements). Or it drifts badly (agents anchor on yesterday's
converged list and stop genuinely re-examining, i.e. groupthink induced by the
loop's own history). Both are real outcomes worth knowing about — this question
matters beyond Curator, since any synthesis loop with memory of its own past
runs (weekly synthesis, exploration threads revisited) could exhibit either
pattern.

**Approach:** start with a single day, ungated by history (clean baseline, Gates
1–3 as above). Then run forward day by day, *introducing* prior-day output as
context starting from day 2, and watch whether overlap/divergence patterns
(Gate 1 metric) and convergence behavior (Gate 2) stay stable, improve, or
degrade over the sequence. The shape of *how many days* and exactly what prior
context is exposed is intentionally left open — this is a question for the POC
planning round, not this document.

---

## Defining "best" — open and unresolved (provisional thinking, not decided)

*Everything in this section is provisional as of 2026-06-30. It reflects
current thinking, not a decision, and needs further refinement before it
constrains any POC plan.*

"Produce one agreed top-20" (Gate 2) only means something if "best" is defined
*before* the agents argue, not discovered by them mid-debate. Otherwise three
models silently apply three different private rubrics, and the resulting
"agreement" is just an averaging of unstated criteria — the negotiated-
compromise failure mode (Gate 2, above), relocated one step earlier and harder
to see.

**Current thinking:** all three agents should share one stated rubric —
essentially the criteria the current pipeline already encodes implicitly in
its scoring weights (relevance, source credibility, timeliness, variety, depth
vs. trending) — made explicit as a shared brief. The experiment then tests
where agents disagree about how a *specific article* scores against a *shared*
standard, not whose private taste wins. The rubric itself still needs to be
written out; this document only asserts that it needs to exist and be shared.

**What agents are allowed to know, when applying that rubric, splits into
several different kinds of context — each with different cost and risk, not
one bucket:**

- **Previous lists** — covered by the multi-day memory question above. Staged
  in from day 2 onward, not available on day 1.
- **The domain and the rubric itself** — always available; this is the shared
  brief, not "research."
- **Outside reality** — reputable sources beyond the 700 candidates, the
  broader marketplace of ideas, trends, late-breaking news. This is the
  genuinely new and consequential piece: it would let an agent argue "this
  stays — three other reputable outlets are independently confirming this
  right now" or "this goes — already stale relative to a development an hour
  ago." Closer to what an actual editor does, and a direct, falsifiable lever
  against hallucinated reasoning ("I checked, this is corroborated" vs. "I
  think this is more relevant"). But it means agents doing live lookups
  *mid-debate*, reopening the cost/runaway concern — multiplied by however
  many lookups each agent makes while arguing over 20 articles.

**Provisional resolution — treat outside-verification as a separate, optional
variant, not folded into Gate 2 by default:**
1. Run Gate 2 first **closed** — agents argue only from the 700 candidates
   plus the shared rubric. Cheap; tests whether disagreement exists at all on
   a closed information set.
2. Only if that's informative, run a second **grounded** version where each
   agent gets a small, fixed lookup budget (not unlimited search) to verify
   claims against outside reality.
3. Compare: does grounding change who wins an argument, or just add cost
   without changing outcomes? Possibly as interesting a finding as the
   original three-gate comparison.

---

## Provisional rollout sequence (NOT decided — needs refinement)

*This section is explicitly tentative — one reasonable way to sequence the
work, written down so it isn't lost, not a committed plan.*

Two different things have been informally called "steps" in discussion and
need to stay distinct so the doc doesn't conflate them:

- **The three gates (1/2/3) are stages within a single day's run, not a
  multi-week phasing.** They're sequentially dependent — Gate 3 is the only
  thing that tells you whether any of it was worth doing, so it shouldn't be
  skipped wholesale. What *can* flex is Robert's active effort: every day
  could run all three gates and log results, while Robert's hands-on blind
  judgment (Gate 3) happens on a sampled subset of days rather than every
  single one (see open question 3, below).
- **Closed vs. grounded (above) is the axis that actually supports "run one
  for a while, evaluate, then consider the other."** Provisional sequence:
  run closed-information only for an initial period (e.g. a week), evaluate
  whether Gate 1 shows real divergence and whether Gate 2's agreement process
  is informative on a closed set, and only then decide if the grounded
  variant is worth its added cost.

**A practical note on the "week" framing:** roughly five to seven daily runs
through Gates 1–3 is also exactly the dataset needed for the multi-day memory
question (does access to history stabilize or cause drift). One period of
data could serve both questions — but a given day's result could then be
explained by at least three different things: ordinary day-to-day variation,
memory-induced drift, or the closed-information ceiling. How to disentangle
these after the fact is itself unresolved and flagged for refinement, not
solved here.

---

## What this document deliberately does NOT decide

- The actual rubric text for "best" (only that one must exist and be shared — see above)
- Whether outside-verification is used at all, and if so, the lookup budget per agent
- Exact number of days or rounds
- Which specific models, in what roles
- Cost ceiling figures
- Where the script lives / runs (local Mac, AWS, sandboxed)
- Whether real production data or a sampled subset is used first
- Whether this ever gets wired into production Curator, even if it tests well

All of the above are POC-planning decisions, deferred to the next round(s) of
review. The "provisional" sections above are first-draft thinking toward some
of these, not the decisions themselves.

---

## Open questions for the next review round

1. Does the Gate 1 overlap metric (Jaccard or similar) actually capture
   *meaningful* divergence, or just noise — and is qualitative tagging
   (above) enough to tell the difference, or does it need a more structured
   taxonomy of disagreement types?
2. How much prior context (full transcript? just the final list? a summary?)
   should later days see, once the multi-day memory question is explored?
3. Is Robert's blind judgment (Gate 3) practical to do repeatedly across many
   days, or does it need to be sampled (e.g. blind-judge every 3rd day)?
4. Does this case study's finding generalize beyond Curator — i.e. if
   multi-model debate-with-memory turns out to add real value here, does that
   change how we think about weekly synthesis or exploration-archetype
   processing (both flagged elsewhere as candidates for unsupervised
   agent loops)?
5. At what point (if any) does this stop being a side experiment and become a
   candidate architecture change for Curator itself?
6. What does the shared rubric for "best" actually say, concretely? This needs
   to be written before Gate 2 can run at all.
7. Is the closed-vs-grounded distinction worth the added complexity, or should
   the case study just commit to one mode for the first run rather than
   planning for both upfront?

---

## Relationship to other current design threads

- **AutoResearch reference pattern** (noted in memory/intent layer design
  discussion) — this case study is the concrete test of that pattern's
  transferability, not a separate idea.
- **Model-agnostic principle** — using different model families on identical
  prompts is a direct, practical exercise of this principle, not just a
  philosophical commitment.
- **Cost discipline** — the gate structure exists specifically so this doesn't
  become a runaway-cost experiment; each gate is a checkpoint, not a
  formality.
- **Exploration archetype** — if this pattern proves valuable, it's a strong
  candidate for how exploration's divergent, cross-pollinating threads could
  eventually be processed (unsupervised variation generation, reviewed in
  batches) — but that is a future thread, not in scope here.

---

*Idea-stage case study doc · 2026-06-30 · Claude.ai · pending 1-2 more review
rounds before any POC plan*
