# Curator Domain Roadmap

**Draft v1 — 2026-09-02**

| | |
|---|---|
| **Status** | Draft for Robert's review, then multi-agent review per the 2026-08-26 protocol |
| **Author** | Claude.ai (design) |
| **Supersedes** | Nothing. First domain-level roadmap for Curator; sits under the root ROADMAP. |
| **Downstream** | Claude Code for build-spec mechanics; items here are not build specs and carry no Definition of Done or Commit sections yet |
| **Conventions** | Three tiers (Committed / Planned / Aspired), no deadlines, every item carries a capability, paths-not-taken preserved |

---

## Intent

Curator was built as a geopolitics-and-finance reading practice. It has outgrown that in one direction the current design cannot follow: **Robert can read what Curator finds, but he cannot tell Curator what to look into.**

Every rung of the existing ladder — briefing, scan, deeper dive, research thread, leanings — is initiated from an article Curator already surfaced. There is no entry point for a subject that arrives from outside the funnel. Today's terminal-Pleistocene work is the proof: a substantial, versioned research artifact was produced entirely outside Curator, with no way to hand it back in.

This roadmap does three connected things:

1. **Add the missing inverse of the funnel** — a structured way to inject a subject and have Curator run with it.
2. **Remove the topical ceiling** — move Curator from a two-topic domain to an open tagged domain, with subject areas as tags rather than boundaries.
3. **Make the daily briefing genuinely diverse** once it spans subjects, rather than letting geo/finance volume drown everything new.

The binding insight is that these are one item, not three. Injection without tagging produces subjects with nowhere to live. Tagging without diversity work produces tags that starve in the ranking. Diversity without injection produces a wider net over the same 23 feeds.

---

## The gap, stated precisely

Curator is entirely **pull**. Sources are fixed, scoring is fixed, and the reading ladder begins wherever the scoring lands.

```
sources → scoring → briefing → scan → deeper dive → research thread → leanings
```

Everything Robert can act on is downstream of the briefing. The five active research threads — empire-landpower, china-rise, gold-geopolitics, strait-of-hormuz, hellscape-taiwan-porcupine — all originated from articles Curator found, and all sit inside the original two topic areas. That is not a coincidence; it is the architecture expressing itself.

What is missing is a **push** path:

```
subject → seed → thread → sessions → dive → leanings
                    ↑
              re-injection
```

The seed enters at thread level, skipping briefing and scan entirely, because the subject is already known and the framing already exists.

---

## Why today's briefing is the right seed format

This is the part worth dwelling on, because it means the format problem is already solved and was solved by accident.

The terminal-Pleistocene briefing was written to be readable. But structurally it is almost exactly what a Curator thread needs at birth:

| Briefing element | Maps to |
|---|---|
| Claim ladders with confidence levels | Leanings register entries, pre-populated and already ranked by how settled they are |
| Open threads list | Search queries for the next research session |
| Annotated bibliography | Seed sources for the trust-tier classifier, with the verified/unverified flag already set |
| Version history and corrections log | The re-injection mechanism — a diff against pinned prior state, not a fresh dump |
| Position-movement table | The thinking record Curator's framing already says it exists to keep |
| Confidence markers | Scoring weights for what a session should chase first |

Nothing here needs inventing. **The seed format is the briefing format**, and the discipline that makes it work — versioned, corrections logged, positions tracked, citations flagged for verification status — is the same discipline the spec-review protocol already established on 2026-08-26.

That protocol should be reused rather than paralleled: SHA-256 pin at injection, reviewers return separate artifacts rather than editing in place, Robert remains the sole promotion point. A research seed and a build spec are different content under the same governance.

---

## The two loops, unchanged in shape

Per the root roadmap's bounded-loop commitment, Curator's loop stays Curator's. Widening the subject range does not widen the loop.

```
reading & reactions → scoring and threads adapt → next briefing fits better
```

```
subject arrives from life → seed → thread runs sessions → dive → leanings → subject stays current
                                                                      ↓
                                                            re-injection with new input
```

Both close inside Curator. Neither reaches into Guild, CoS, or the language domains. The anti-drift commitment holds: a Pleistocene leaning never becomes a work suggestion.

---

## The boundary question, and the answer

Opening Curator to any subject raises a real risk: Curator becomes the everything-domain, and the domain boundaries the root roadmap calls intentional stop meaning anything.

**The boundary should move from topical to modal.** What makes something Curator is not its subject but its *mode of engagement*: read something, react to it, scan it, dig, accumulate a library, form a revisable lean. German practice is a different mode. A CoS decision is a different mode. A build defect is a different mode.

Stated as a principle for this roadmap:

> **Curator is a reading and research practice, not a subject list.** Anything Robert wants to read into, track over time, and form revisable positions on belongs here. Anything he wants to *do* belongs elsewhere.

That removes the topic ceiling without dissolving the boundary, and it is consistent with the existing framing — informed reading and research in service of decisions and of tracking learning and mistakes over time — with the two named topics dropped from the definition.

---

## Design tension to resolve: tags vs. instances

**This needs Robert's decision and is the most consequential fork in the document.**

The root roadmap's Planned tier already contains *Curator topic-area instances — health first*: instantiate the converged template on a new topic area, near copy-paste on search and scoring, with new topic-appropriate inputs such as wearable history. Same pattern as German → Portuguese → French.

This roadmap proposes the opposite architectural move: **one Curator, open tags, no hard boundaries.**

These are genuinely alternative designs and should not both proceed unexamined.

**Proposed reconciliation — instances for different *inputs*, tags for different *subjects*:**

- Health wants its own instance because its **inputs differ in kind** — wearable time series, risk-monitor history, personal metrics. That is not an RSS-and-web-search problem; it is a different acquisition and scoring core wearing the same UI.
- The terminal Pleistocene does not want an instance. Its inputs are the same inputs Curator already has — journals, web search, citation chasing. Only the subject differs. An instance would duplicate the entire apparatus to change a keyword list.

**Test to apply to any future candidate:** does it need new *sources of a new kind*, or only new *subjects from existing kinds*? New kinds → instance. New subjects → tag.

If Robert accepts this, the health instance survives unchanged in the root roadmap and this item does not conflict with it. If he prefers instances throughout, this roadmap's tagging work shrinks to briefing diversity alone and the injection path becomes an instance-creation path instead. **Open question 1.**

---

## Tier: Committed

Sequenced in order. No dates — dates belong to the build queue.

### C1. Subject injection

**Capability:** Curator gains an entry point that does not depend on what its sources happened to surface.

A structured seed document creates a research thread directly, bypassing briefing and scan. The seed carries subject framing, initial questions (from the open-threads list), seed sources (from the bibliography), initial positions with confidence levels (from the claim ladders), and tags.

**Re-injection is part of the item, not a follow-on.** The stated use case is returning with external review — Grok's and ChatGPT's comments on a later version — and folding it into a live thread. A seed is therefore versioned from the first write, and the second injection is a diff against a pinned prior version rather than a replacement.

Design points that matter:

- **Seeds are authored outside Curator and handed in.** They come from wherever Robert actually does this work — a chat, an editor, another agent. Curator's job is to receive, pin, and run, not to host an authoring environment. This keeps the item small and matches how the terminal-Pleistocene artifact was actually produced.
- **Injection is a governed act,** using the 2026-08-26 protocol: hash-pinned at injection, comments returned as separate artifacts, Robert the sole promotion point.
- **The seed's bibliography feeds the trust-tier classifier** as a proposal, not an automatic promotion. Eighty-one classified domains exist today; a seed adding academic publishers and repositories should propose, and Robert or a rule should accept.
- **Confidence levels drive session priority.** An open thread marked as the sharpest live tension should be chased before one marked resolved.

**Not in scope:** an authoring UI, automatic seed generation from dives, or seeds that write to any domain outside Curator.

### C2. Open tagged domain

**Capability:** subjects are tags, not boundaries; new subjects cost nothing structural.

Tags replace the fixed geo/finance framing across threads, scans, dives, leanings, and sources. Existing content is tagged retroactively — the five live threads map cleanly to geopolitics and finance tags without reclassification pain.

Design points:

- **Tags are flat and additive.** No hierarchy, no controlled vocabulary at the start. If a vocabulary is needed it should emerge from use, per the platform's store-richly-tag-lightly convention.
- **Sources get tags too,** which is what makes per-tag acquisition possible in C3. The 23 hardcoded feeds are all geo/finance; a Pleistocene thread needs sources those feeds will never carry.
- **This is quietly the on-ramp to the graph.** The root roadmap gates Neo4j on 20+ sources tagged. An open tagged domain is precisely the mechanism that generates tags at volume, which makes this item load-bearing for the one aspiration Robert says he actually cares about. Worth stating in the item rather than discovering later.

### C3. Briefing diversity

**Capability:** the daily briefing reflects the breadth of what Robert is actually reading into, not the volume of what his feeds happen to publish.

This is the item most likely to be under-scoped, so stating the mechanism plainly: **diversity is a composition problem, not a ranking problem.** Twenty-three geo/finance feeds producing ~700 daily candidates will always outvote a new tag in a pure ranked list. Ranking harder does not fix it.

Two mechanisms are needed:

1. **Per-tag source acquisition.** A tag with no sources produces nothing to rank. New tags need their own feeds, search keyword sets, and — for research subjects — the OpenAlex citation chasing that research-intelligence sessions already do.
2. **Slot allocation in briefing composition.** The briefing reserves representation across active tags rather than taking the global top 20. Precedent exists: source-variety caps were already built and fixed for German (Lesen) and Portuguese (Leitura). Same pattern, applied to tags instead of sources.

**Stated risk, with mitigation.** Forced diversity can make the daily briefing *worse* for the use Robert actually has. Geo/finance is not just legacy volume — it is what he reads for decisions. Mitigation: tag weighting stays under his control, and the briefing should be able to show what diversity cost — which item was displaced and by what. Not a hidden trade.

---

## Tier: Planned

**P1. Seed emission from dives.** Today a deeper dive does not emit candidate threads, and thread creation is its own branch from the scan. Once seeds exist as a format, a dive is a natural seed producer — it already synthesizes across an accumulated library and produces exactly the claim/question/bibliography shape a seed needs. Deliberately not committed: the manual path should prove itself first.

**P2. Leanings into the briefing loop.** Leanings currently do not feed AI Observations, and Observation responses are stored `acted_on: false`. With tags in place, a leaning becomes a legible signal about what Robert is actively thinking about — and therefore a scoring input. This closes the Curator loop properly rather than leaving leanings as a terminal write.

**P3. Non-anglophone and academic source expansion, per tag.** Currently an aspired item in the root roadmap. Tagging makes it concrete rather than aspirational: the Pleistocene bibliography alone points at Spanish- and Portuguese-language regional venues and the Russian-language mammoth-steppe tradition, neither of which any current feed reaches. Robert reads Portuguese and Spanish; this is usable capability, not completeness for its own sake.

**P4. Thread health observations.** An AI-Observations-style periodic pass over the threads themselves: which have gone stale, which have unresolved open threads, which have not been re-injected since their last external review. Mirrors the build-health observations capability already committed for Guild, and satisfies the standing principle that every item carries a capability and cleanup ships prevention.

---

## Tier: Aspired

**A1. Tag graph.** The headline root-roadmap aspiration — graph plus data store closing the loop on personal intelligence — expressed in Curator terms: subjects, sources, threads, leanings, and their relationships as a traversable structure rather than a set of lists. Gated on the same 20+ tagged sources trigger, which C2 is the mechanism for reaching. Promotes to Planned as soon as sequencing allows.

**A2. Cross-thread synthesis.** Deeper Dive synthesizes within one thread's library. Across threads, with tags as the join, the question becomes what one subject implies for another. Genuinely valuable and genuinely premature — it needs the graph first.

**A3. Multi-agent review as a Curator capability.** Today external review is Robert manually carrying a document to Grok and ChatGPT and carrying comments back. The Challenger pattern already live in the Deep Dive pipeline is the ancestor. Making review a first-class thread operation is real capability, but it should wait until re-injection has been exercised manually enough times to know what the workflow actually is.

---

## Paths not taken

| Considered | Why not | Revisit if |
|---|---|---|
| Curator as a general research assistant | The value is the *compounding thread library*, not on-demand answers. On-demand research already exists in the tools Robert uses daily; rebuilding it inside Curator adds nothing and dilutes what is distinctive. | Never, on current understanding |
| Authoring seeds inside Curator | Seeds get written where the thinking happens — a chat, an editor, another agent. A UI would be a second-class version of tools that already work. | Injection proves valuable and hand-off friction becomes the binding constraint |
| Hierarchical tag taxonomy | Premature structure. Flat tags cost nothing and a vocabulary can emerge from use. | Tag count grows past the point where a flat list is browsable |
| A separate "research" domain alongside Curator | Same mode of engagement, same ladder, same apparatus. A new domain would duplicate Curator to change a keyword list — and the root roadmap's no-new-domains-except-French principle applies. | Never, on current understanding |
| Auto-injecting subjects from CoS or Guild | Violates the bounded-loop commitment. Robert decides what he reads into. | Not anticipated |

---

## Open questions for Robert

1. **Tags or instances?** (§ Design tension.) The proposed reconciliation is instances for new *input kinds*, tags for new *subjects* — which preserves the health instance already in the root roadmap. Confirm or override.
2. **Naming.** "Injection" is the working word and it is a mechanical term for what is really the act of handing Curator something you care about. LEANINGS was named deliberately and well; this deserves the same attention. Candidates: **Seed**, **Dossier**, **Commission**, **Brief**. Robert's call.
3. **Does the framing statement change?** Curator is currently described as informed reading and research *in geopolitics and finance*. This roadmap proposes dropping the two named topics and keeping everything else. That edit touches the README, ARCHITECTURE, and the root ROADMAP — worth confirming before it ripples.
4. **Should the terminal-Pleistocene thread be the first seed, or a test seed?** First real use has a way of hardening whatever format it touches. The dual-agent lesson from 2026-07-20 was explicitly *don't use a real thing as the vehicle for learning a new workflow.* A throwaway seed first may be the cheaper path.
5. **Briefing diversity — what does Robert actually want to see daily?** C3's slot allocation needs a target shape. Equal representation across active tags is one answer; a weighted default with geo/finance heavier is another; explicit per-tag weights he sets is a third.

---

## Prerequisites and known interactions

Not blockers, but Claude Code should confirm each before spec-writing:

- **Deep Dive consolidation** (root roadmap, Planned). Generation has fragmented into multiple coexisting scripts with only one live. Building seed emission (P1) on top of that fragmentation would compound it. Consolidation should land first or be scoped into P1.
- **Model configuration** (spec_125, open). Curator's `--model=` flag is silently broken in production — cron passes `grok-4.3`, which is not a recognized value, so it falls through to a default and has been correct by coincidence. Any new pipeline should not inherit that pattern.
- **AI Observations scheduling** (unverified post-AWS-migration). P2 routes leanings into that pipeline; its scheduled invocation has not been re-verified since the migration and local outputs stop in April. Verify before depending on it.
- **Data layout.** New per-tag artifacts follow the `data/curator/` convention established in the Phase 2 reorg, and JSON stays source of truth with Postgres as rebuildable projection.
- **Issue #158 lesson.** Briefings broke for two days when cron referenced moved file locations, caught only by an unrelated live audit. Any new scheduled path needs an explicit failure state and a live smoke check that confirms output was actually produced — not that an endpoint returned 200.

---

## What Claude Code needs from this document

This is a roadmap item, not a build spec. It carries no Definition of Done and no Commit section, per the build-specs convention — those belong to the specs that come out of it.

Suggested decomposition into specs, one per committed item, in order:

1. `spec_curator_subject_injection` — seed format, versioning, pinning, thread creation, re-injection diff
2. `spec_curator_tagged_domain` — tag model, retroactive tagging of existing content, source tagging
3. `spec_curator_briefing_diversity` — per-tag acquisition, slot allocation, weight controls, displacement visibility

Each should follow the Intent-before-Overview convention and carry its own Definition of Done and Commit sections.

**Recommended review path:** this draft goes to Robert first, then through the 2026-08-26 multi-agent protocol — hash-pinned, reviewers returning separate artifacts, asked to challenge rather than agree. The tags-versus-instances fork (open question 1) is the item most worth challenging, and reviewers should be pointed at it explicitly.

---

## Version notes

**v1 draft — 2026-09-02.** First domain-level roadmap for Curator. Originated from Robert's terminal-Pleistocene research project, which demonstrated both the gap (no injection path) and the solution (the versioned briefing format is the seed format). Five open questions outstanding; the tags-versus-instances fork is unresolved and blocks C2 scoping.
