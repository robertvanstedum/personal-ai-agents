# Curator — Research Area Concept (Iteration 2)

*Concept document, not a build plan. Nouns, flows, principles — no screens, files, or APIs. Supersedes Iteration 1. A PLAN doc follows from this.*

**Date:** 2026-05-30 · **Status:** Concept stable, ready for PLAN · **Note:** requires backend/pipeline changes when it graduates to a PLAN (unlike the parallel visual redesign, which rides untouched APIs).

---

## Domain purpose

Curator exists to make **better decisions under complexity and uncertainty**, within a bounded territory. Learning is the companion goal, not the headline.

The method it serves is **step, check, adjust, avoid** — not "be right." In a complex system the same action can produce unexpected results and visibility is always narrow; you act in a held-loosely direction, watch, and adjust. Curator is where that method gets practiced on real questions (geopolitics, finance, investing leanings). This is the same idea as the corporate "we make decisions, not just follow a process" — which is why Curator's pattern eventually informs Guild.

---

## The governing principle: spend follows attention

The single constraint that prevents this from bloating into another dense research app: **the system spends money and attention in proportion to yours.** Dormant topics cost nothing. Active pulls run only while you're engaged. Re-entry costs a cheap Scan until you ask for more. Every expensive action is gated behind an explicit choice.

Every feature, field, and action in the eventual build must be tested against this. Anything that spends without your engagement violates it and gets cut. This is the answer to the scope-creep risk both reviewers flagged.

---

## The boundary (the guardrail)

**Geopolitics and finance, read structurally, at any horizon** — short, mid, and the long waves that are hard to see but where structural shifts often live.

Defined by its exclusions, which are about *triviality*, not subject:
- **Not** partisan politics or debate (the structural game between powers, not the daily political scrap).
- **Not** generic economics (the applied structural read, not textbook theory).
- **Not** day-trading noise (trends and leanings, not ticks; even investment decisions are incremental positioning, not trading).

Focus is a feature. Out-of-territory surfacing is a rare, deliberate exception, by manual promotion only.

---

## The gathering tier — what you take in

Neutral input: facts, learning, curiosity. Frictionless. Most of the activity.

- **Source** — anything bearing on the territory: article, X post, paper, **or book**, new or old. Origin (Curator-found / pulled / added by you) and recency are *attributes, not filters*. A 1941 book is a Source if it echoes a live theme.
- **Topic** — a specific subject under investigation ("the Quad"). Has a life and a **state** (below). Accumulates Sources, Takes, Notes — and over its life accumulates a *sequence of dives*, building a narrative.
- **Take** — analysis, in two depths:
 - **Scan** (was "deep dive") — fast, low-cost, informative, with a further-reading appendix.
 - **Dive** (was "deeper dive") — runs longer, thinks harder, costs more, manually triggered, cost-monitored.
 - *Naming change kills the deep-dive / deeper-dive confusion; must be atomic in one commit.*
- **Note** — your voice in the record, frictionless: a **reaction** to a Source ("I disagree, here's why") or **open wondering** ("interesting, want more"). The reaction flavor doubles as a judgment signal.

### Topic states (new — this is the core of Iteration 2)

A Topic moves between three states; you control the movement:

- **Dormant / on radar** — costs nothing, indefinite (no expiry), rides the daily stream. Surfaces "on radar" finds when something relevant appears. The natural resting state for slow topics (the Quad may be silent for months). Permanent until you dismiss it.
- **Active pull** — paid sessions for dynamic, fast-moving news (the Iran war, supply chains, Gulf/China liquidity). **Engagement-gated:** a configurable auto-stop at **10–14 days** as a safety net against forgotten spend, *plus* a **manual pause** you fire when your attention must go elsewhere but you don't want to forget the thread.
- **One-shot dive** — a deeper pull right now, no standing pull. Scan → Dive → done, dive again later if you choose.

A Topic flows between states as interest and the world change. The Quad lives mostly **dormant** with occasional **one-shot dives** that stack into a narrative; Iran lives in **active pull** while hot, **pauses** when you look away.

### Re-entry (the catch-up rule)

Returning to a paused or dormant Topic follows the Scan→Dive ladder: the system gives a cheap **Scan** of what accumulated while you were away; **you** decide whether it earns a **Dive**. The expensive step is always your explicit call — coming back never costs a surprise bill.

### Where radar finds appear (no duplication)

An "on radar" find always carries the radar flag. *Where* it shows is routed by newsworthiness, and it appears in exactly one place:
- **Newsworthy** (India + Australia make a dramatic move on China) → into the daily top 20, flagged as a radar hit.
- **Quiet/slow** (a relevant think-tank piece) → into a **dormant section**, a holding area you peek at periodically, so it doesn't crowd the 20.

One item, one place, never both. The newsworthiness threshold is the router (tunable later).

---

## The grouping layer — the bridge between tiers

A Topic often starts specific but matters as *part of something broader*. The Quad isn't really about the Quad — it's a data point in a larger trend, or evidence for a view you're validating.

**Topics (and Sources) carry 2–5 tags** — multiple because the material has multiple components (the Quad is China-containment *and* multipolar-realignment *and* gold-relevant), bounded because unlimited tagging produces mush where everything connects and nothing means anything. The cap is a thinking discipline: name what a thing is *actually* about.

Tagging groups Topics. **A group is:**
- a **Theme** when you cluster neutrally to study a broader area, or
- a **Leaning** when you hold a directional view about that area you want to validate.

A Theme *becomes* a Leaning the moment you form a view, and softens back if you lose conviction. Grouping can be **your hand or AI suggestion** ("your Quad and gold-geopolitics topics keep citing the same dynamic — group them?"); you're always the gate.

### Pull altitude is a choice

Research can run at two altitudes:
- **Narrow** — one Topic, just that question.
- **Contextual** — the whole group, its shared context feeding the search (the Quad researched *as part of* the realignment story, not in isolation).

You pick per pull. This is a real difference in what the search does, so it matters for the backend.

---

## The testing tier — what you're deciding

- **The Leaning** — a grouped set of Topics (plus loose Sources) you hold a directional view about. Not a separate floating object: it's *what a Topic-group becomes when you have a stake in it.* It spans a confidence gradient:
 - **Question** — open, no weight. "Does gold play a bigger role in a financial reorganization?"
 - **Lean** — a directional preference under uncertainty. "More gold good, more dollar exposure bad — not sure, but I lean this way." The honest middle no current tool has a home for.
 - **Hold** — firmest, with stakes/actions attached. "Buy gold incrementally, hold a bulk now." ("Hold" over "Position" — less trading-specific, means *where I stand now*.)

 A Leaning **firms and softens** as evidence shifts, and can move backward. No verdict, no right/wrong scoreboard — a tracked direction. It must make recording *contradicting* evidence and changing your mind **easy**, or it becomes a bias engine — the opposite of Curator's anti-feed soul.

 A Leaning owns: its evidence record (supporting vs. complicating), its confidence state, your notes, and the teammate read. Its **evidence is both auto-gathered** (the system surfaces relevant Sources from its grouped Topics) **and manually attached** (you add anything you find relevant). It does **not** run its own pull — it draws from its Topics' sessions, so it never duplicates cost.

---

## The AI's role: a teammate, not a judge

At the gathering tier the AI summarizes neutrally (Scans, Dives). At the testing tier its job is explicitly **not adversarial** — Robert rejected devil's-advocacy. It's a teammate looking at the same board, giving two things together:

> an **honest assessment** (which way the evidence leans — confirming *or* complicating) **+ the implication for the next move** ("this isn't going as expected, here's why, so consider ABC / be cautious / push harder").

It confirms when evidence genuinely supports and counters when it doesn't — both honestly, because flattery and contrarianism are equally useless with a real decision on the line. An opinion, not a verdict. **You move the firmness; the system informs.**

**Trigger:** on-demand ("get teammate read"), with a free **notification badge** when new Sources have arrived since the last read. The badge costs nothing (just a count); the read is your explicit, cost-monitored call (~$0.01–0.02). Preserves the budget discipline already built in.

---

## How Sources get in (promotion by tag)

Old and new works enter the same way — **tag it and send it.** No book-corpus crawl; search stays simple. You are always the gate:

1. **Your own reading** → add it with your note (Burnham's *Managerial Revolution*; reread *Road to Serfdom*). Origin = you.
2. **A feed article *about* a work** → a reading list surfaces in the stream; tag the work out of it.
3. **A Scan/Dive appendix entry** → the further-reading list already spans old and new; promote a relevant entry.

**Relevance test for an old source:** not "matches today" but **"echoes a live theme."** Burnham *rhymes* with today's structural question. So classics attach mostly to the testing tier (Themes/Leanings), rarely to the daily brief.

**Surfacing rule — by cost to act on:** readable-and-free (web articles, AI further-reading links) surface directly, even into the daily top 20, no gate. Costly-to-act-on (a book to acquire and read; a Dive to run on API spend) gets a conscious decision step — the same cost-discipline already practiced on Dives, applied consistently.

---

## How the graph fits (Neo4j) — two kinds of edges

The platform spine's graph layer (Postgres + Neo4j, shared with Guild) earns its place here in two distinct jobs:

**Declared edges — created by tagging.** When you tag the Quad with two themes and attach it to the gold Leaning, you author edges: Topic→Theme, Source→Leaning, Topic→Topic (via shared tags). Explicit, high-confidence, yours. Tagging *is* edge creation. This is nearly free to build and can live in Postgres tables now, shaped to migrate to graph nodes/edges cleanly later (the "data pre-formatted for the database" discipline).

**Discovered edges — found by traversal.** The graph traverses your declared structure and surfaces connections you *didn't* draw: "your Quad and supply-chain topics are two hops apart through gold-geopolitics — you never linked them directly." Inferred, lower-confidence, offered as **AI suggestions you accept or ignore.** This is what justifies a graph database over a tags column: *Postgres can store tags; it cannot cheaply answer "what's two-to-three hops away through shared structure across everything I care about."* That traversal is the backtrace/"what connects to what" question from the VISION doc.

**Two phases, matching the platform roadmap:**
- **Now:** the declared layer — 2–5 tagging, grouping into Themes/Leanings, multi-membership. Postgres-shaped for migration. Neo4j need not be running to *capture* edges; tagging generates them.
- **Later (v1.3 intelligence layer):** activate Neo4j *traversal* over the accumulated edges to surface discovered connections. Only produces signal once the declared structure is rich enough — same "don't build the intelligence before the data exists" rule as `german_intelligence.py`.

**The Curator/Guild seam:** declared tagging is pure Curator. The *discovered* cross-structure intelligence — cross-Topic, cross-Leaning pattern and tension detection (the "portfolio tension scan" both reviewers wanted) — is correctly located in **Guild's Chief-of-Staff function**, fed by the edges Curator generates. Nothing is cut; it's put where it belongs.

---

## The one flow (collapsing the seven fragments)

> An article catches you → **Scan it** (quick brief) → optionally **Dive** now, or leave it → make it a **Topic** (dormant by default — on radar, free) → it accumulates Sources, Notes, and a sequence of dives over time → tag it (2–5) so it joins **Themes**/Leanings → when something's dynamic, switch it to an **active pull** (engagement-gated); when it's slow, it sits dormant and pings you → if you hold a view, the group is a **Leaning** you validate, with auto + manual evidence and the on-demand **teammate read** → the Leaning firms toward **Hold** or softens. Step, check, adjust.

Two registers, one gradient: a neutral dormant Topic at the loose end (explore), a held Leaning at the firm end (decide). Never forced between them — you promote when something earns it.

---

## What this is not

- Not a feed to scroll. Bounded, focused, anti-firehose.
- Not a prediction scoreboard. No right/wrong grading — only tracked direction under uncertainty.
- Not recency-bound. A relevant 1941 book outranks an irrelevant article from this morning.
- Not a second note-taking app. Every field serves the one flow or gets cut (the spend-follows-attention test).
- Not everything in the world. Geopolitics and finance, structurally read. The exclusions are part of the definition.

---

## Decided since Iteration 1

- **Naming:** Scan / Dive (was deep-dive / deeper-dive); Question → **Lean** → **Hold** (was Position).
- **Topic states:** dormant / active / one-shot — *new.*
- **Engagement-gating:** auto-stop 10–14 days + manual pause — *new.*
- **Radar routing:** newsworthy → top 20 (flagged); quiet → dormant section; never both — *new.*
- **Grouping bridge:** 2–5 tags; group = Theme (neutral) or Leaning (view); AI may suggest groupings — *new.*
- **Pull altitude:** narrow vs. contextual, your choice per pull — *new.*
- **Leaning evidence:** both auto-gathered and manually attached.
- **Teammate read:** on-demand + free badge.
- **Graph:** declared edges now (Postgres-shaped), discovered edges later (Neo4j traversal); portfolio/tension scan located in Guild.

## Still open for the PLAN

- Exact tag taxonomy: free-form tags, or a curated controlled vocabulary, or both?
- The newsworthiness threshold that routes radar finds — how is it set/tuned?
- Whether "Theme" needs to be a visible object now, or is implicit (just a shared tag) until a view turns it into a Leaning.
