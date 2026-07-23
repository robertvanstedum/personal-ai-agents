# Curator — Research Area Concept (Iteration 1)

*Concept document, not a build plan. Nouns, flows, and principles only — no screens, files, or APIs yet. Those come in a PLAN iteration after the concept holds.*

**Date:** 2026-05-29 · **Status:** Draft for Robert's review · **Note:** unlike the visual redesign, this concept will require backend/pipeline changes when it graduates to a PLAN.

---

## Domain purpose

Curator exists to help make **better decisions under complexity and uncertainty**, within a bounded territory. Learning is the companion goal, not the headline.

This is the line that separates Curator from the other domains: Mein Deutsch builds capability, Guild builds the operating model, Curator sharpens decisions. The method it serves is not "be right" — in a complex system the same action can produce unexpected results and visibility is always narrow. The method is **step, check, adjust, avoid**: take the next step in a held-loosely direction, watch what happens, adjust. Curator is where that method gets practiced on real questions.

---

## The boundary (the guardrail)

Curator's territory is **geopolitics and finance, read structurally, at any horizon** — short, mid, and the long waves that are hard to see but where structural shifts often live.

It is defined as much by its exclusions, which are about *triviality*, not subject:
- **Not** partisan politics or debate (geopolitics — the structural game between powers — not the daily political scrap).
- **Not** generic economics (the applied structural read, not textbook theory).
- **Not** day-trading noise (trends and leanings, not ticks; even when the action is an investment decision, it's incremental positioning, not trading).

The territory is a domain of responsibility, like a team that owns one IT system: many different activities, all bounded to that system. Focus is a feature. Surfacing something outside the territory should be a deliberate, rare exception, not the default.

---

## Two tiers

The research area has two distinct tiers, and the core problem with the current design is that one activity is scattered across seven fragments instead of organized into these two.

### Gathering tier — what you take in
Neutral input: facts, learning, curiosity. No stakes required. This is most of what you do, and it must stay frictionless.

- **Source** — anything that bears on the territory: an article, an X post, a journal paper, **a book** — new or old. Origin (Curator-found / thread-pulled / added by you) and recency are *attributes, not filters*. A 1941 book can be a Source if it echoes a live theme.
- **Topic** — a neutral subject under investigation ("the Quad"). Accumulates Sources, Takes, and Notes. Can have a time-boxed pull.
- **Take** — analysis of a Topic or Source, in two depths:
 - **Brief** (today's "deep dive") — fast, low-cost, informative, with a further-reading appendix.
 - **Deep Study** (today's "deeper dive") — runs longer, thinks harder, costs more, manually triggered, cost-monitored.
 - Same family; genuinely different tiers. The naming should signal *quick vs. deep*, not *deep vs. deeper*.
- **Note** — your own voice in the record, frictionless, two flavors: a **reaction** to a specific Source ("I disagree with this part, and here's why") and **open wondering** ("no idea what this is, but it's interesting"). The reaction flavor doubles as a judgment signal to the system.

### Testing tier — what you're deciding
Stakes. A view you hold loosely and are working toward a decision on.

- **The Leaning** — one object that spans a confidence gradient:
 - **Question** — loosest. "Is a financial reorganization coming? Does gold play a bigger role?" Open, no weight yet.
 - **Leaning** — a *directional preference under uncertainty*. "More gold good, more dollar exposure bad — I don't know what will happen, but I lean this way." The honest middle that no current tool has a home for.
 - **Position** — firmest. A decision with stakes attached. "Buy gold incrementally, hold a bulk now." Real-world actions hang off this end.

 A Leaning **firms and softens over time** as evidence accumulates and conviction shifts — and it can move backward (a Position softening to a Leaning when evidence turns). That motion *is* step/check/adjust, made into the object's natural behavior. There is no verdict and no scoreboard — this is not about being proven right. It is a tracked direction under uncertainty, with a logic chain you wrote, evidence on both sides, and your evolving notes.

---

## The AI's role: a teammate, not a judge

At the gathering tier the AI summarizes neutrally (Briefs, Deep Studies). At the testing tier its job is different and explicitly **not adversarial**. It is a teammate looking at the same board:

> "This isn't going the way we expected, and here's why — so we should consider doing ABC / being cautious here / actually pushing harder there."

Two parts, always together: an **honest assessment** (which way the evidence is leaning, confirming *or* complicating) and the **implication for the next move**. It confirms when evidence genuinely supports the lean and counters when it doesn't — both, honestly, because flattery and contrarianism are equally useless when there's a real decision. It offers an opinion, not a verdict. **You move the firmness; the system informs.**

A design consequence of "held loosely": the Leaning object must make it *easy* to record contradicting evidence and *easy* to change your mind — or it becomes a confirmation-bias engine, the opposite of Curator's anti-feed soul.

---

## How Sources get in (promotion by tag)

Old and new works enter the same way: **tag it and send it.** The search stays simple — no book-corpus crawl. Three promote-paths, you are always the gate:

1. **Your own reading** → you add it as a Source with your note on why it matters (e.g. Burnham's *Managerial Revolution*, reread *Road to Serfdom*). Origin = you.
2. **A feed article *about* a work** → the recency feed surfaces, say, an Amazon "top books coming" list or a podcast reading list; you tag the work out of it. The system scored a pointer, not a book corpus.
3. **A Deep Study appendix entry** → the further-reading list already spans old and new; you promote a relevant entry into a real Source. Discovery already happened; you're acting on it.

**The relevance test for an old source:** not "does it match today's concern" (it won't) but **"does it echo a live theme or Leaning."** Burnham doesn't report the news; it *rhymes* with today's structural question. So old works attach mostly to the **testing tier** (Leanings/themes), rarely to the Daily brief.

---

## Surfacing rule: free-and-readable vs. costly-to-act-on

What needs a deliberate step is governed by **cost to act on**, not by type:

- **Readable-and-free** (web articles, AI further-reading *links*) → surface directly, including into the Daily top 20. No proposal, no gate. Worst case, you ignore it.
- **Costly-to-act-on** (a **book** to acquire and read; a **Deep Study** to run on API spend) → a conscious decision, because the cost is yours to spend. This is the cost-discipline you already practice on Deep Studies, applied consistently — a book is the human-time version of the same gate.

So an appendix link → surface it freely; an appendix *book* → the "add to Library / propose" beat applies.

---

## Two lenses on one set of Sources

The same underlying Sources are seen two ways:
- **Daily brief** = the *recency* view — what's new in the territory today.
- **A Leaning's evidence** = the *relevance-across-time* view — everything bearing on this direction, regardless of when it was written. This is where the old book lives.

Recency is a Daily-brief concern. It is irrelevant to a Leaning.

---

## The one flow (collapsing the seven fragments)

> An article in the Daily brief catches you → **promote it** (one action, replacing today's scattered Dive / Priorities / Launch-thread / Save) → it opens as a **Topic** with that article as first Source and a quick **Brief** → you can run a **Deep Study**, set a **time-boxed pull** for new Sources, add **your own Sources**, drop **Notes** → if it turns out to matter, you raise a **Leaning** and attach evidence to it from across many Topics and Sources → the AI periodically gives its teammate read (assessment + suggested adjustment) → the Leaning firms toward a Position or softens → step, check, adjust.

Two registers, one gradient: a neutral Topic is the loosest end (explore, curiosity); a committed Position is the firmest (decide, act). You're never forced from one to the other — you promote when something earns it.

---

## What this is not

- Not a feed to scroll. Bounded, focused, anti-firehose.
- Not a prediction scoreboard. No right/wrong grading; only tracked direction under uncertainty.
- Not recency-bound. A relevant 1941 book outranks an irrelevant article from this morning.
- Not everything in the world. Geopolitics and finance, structurally read. The exclusions are part of the definition.

---

## Open questions for iteration 2

1. **Naming.** "Leaning" for the spanning object, with Question / Position as its end-states — does that hold, or does the object want a different name?
2. **Time-boxed pull = Priority-with-expiry?** Your Tigray priority showed "expired 72d ago." Is a time-boxed Topic pull the same primitive as a Priority, built twice? Should they merge?
3. **Where the boundary is enforced** — is "in territory" defined by your declared Leanings/themes, your trusted sources, explicit tags, or a combination? This is what the pull and teammate engines query against, so it's load-bearing for the backend.
4. **Does a Leaning own its own pull**, separate from a Topic's pull? (A Leaning draws from many Topics — does it also pull directly?)