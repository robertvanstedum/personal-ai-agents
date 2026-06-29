# Conceptual Design — mini-moi Long-Term Memory & Intent Layer
*Created: 2026-06-29 · Claude.ai (design/strategy) · synthesizes Robert ↔ Grok dialogue + design session*
*Revised: 2026-06-29 (v2) — folds in exploratory archetype, archetypes-as-product, portable/federated instances, seed personas*

**Status:** Conceptual. For iteration over several days. **Not implementation-ready by design.** Goal: a solid long-term spine we won't have to reshape mid-term.
Nothing here is a spec yet — no Definition of Done, no Commit section, on purpose.

---

## 0. The reframe (read this first) — now in two parts

**0a. It's not a language feature; it's the project's core promise.**
Across the Grok dialogue the subject quietly changed. It started as a *memory layer for language learning* and became a **project-wide memory and intent system spanning all agents and all domains.** As Robert put it, it is *"the overall promise of minimoi.ai."* It maps onto the founding thesis already on record:

> "The cloud LLMs have the world's knowledge — that problem is largely solved. The hard part is acting in your specific situation."

The memory layer **is** the thing that knows the specific situation.

**0b. It's not Robert's memory system; it's THE memory system, and Robert is tenant zero.**
The deeper reframe from the design session: minimoi is built for Robert, but the *architecture* is built to generalize. Three futures are live — (1) a personal tool that makes Robert better; (2) a model of what an AI-native enterprise function could look like; (3) a productized solution other individuals or organizations could run. We build for one and keep generalizability in the architecture always.

**The governing consequence of 0b:** the unit of design is the **archetype**, never the domain. "German" is not a thing we build — it is an *instance* of the learning archetype. "Guild" is an instance of knowledge-work. The test for every design decision becomes: *is this a property of the archetype (the product), or of Robert (the content)?* Robert-isms are content and live in per-user data. Archetype properties are the product.

---

## 1. What we already have (the prototypes are hand-built)

We are not inventing from nothing. Three working ancestors already exist:

1. **Handoff documents.** `handoff_2026-06-28.md` — the one that opened this session — is session-memory *and* intent-memory, written by hand, date-tagged, carrying "where we are / the plan / what we got wrong / first message for the next chat." The shape is proven; the job is automation, not invention.
2. **GUILD_CHARTER.md (v2.1, ratified).** Intent-memory for one domain — the "why and how we operate," slow-changing, curated. The intent layer (§2) is the generalization of this across domains.
3. **Conversas personas (Maria, Carlos, Lucas, Juliana).** Authored-once content that makes an empty interaction immediately usable. This is the seed pattern (§5) one level down — and the direct precedent for seed personas.

Build on all three. They are the design de-risked.

---

## 2. The spine — two layers of memory

Memory is **two different things** with different shapes, lifecycles, and costs. Conflating them is one of the mistakes (§9).

| | **Session Memory** | **Intent / Project Memory** |
|---|---|---|
| Answers | "What was said and done" | "Why we're doing this, where we're going" |
| Form | Append-only transcripts + light metadata | Small, curated, evolving document |
| Lifecycle | Written once, rarely edited | Continuously revised |
| Volume | Grows fast | Stays small on purpose |
| Cost | Cheap to write, expensive to load wholesale | Cheap to load (it's small) |
| Maintenance | Mechanical | Synthesis / judgment |
| Analogy | The git log | The README + the charter |

**The asymmetry that matters:** Robert repeatedly said the part he needs *most* is the intent layer — starting new agent conversations well, not losing the "why," keeping goals in the loop while moving fast. That is **not** retrieval over transcripts. It is a small, well-maintained living document — almost the *opposite* of "big-data memory." The value is in its smallness and curation.

Implication: **the intent layer is the higher-leverage, lower-cost place to start.** Mostly discipline, near-zero infrastructure, two working examples already (handoffs, GUILD_CHARTER).

**Generalizability note:** session memory and learning insights generalize cleanly — same shape for anyone. The intent layer is the *least* generalizable by nature (it's the most personal part of a personal tool). This is fine — each tenant's intent layer is theirs. The risk is only at the seam (§4 membrane).

---

## 3. Archetypes — the unit of design (now THREE)

The domains are real, but they are not the same *kind* of memory. They resolve into three archetypes. **We design archetypes; we instantiate domains.**

| Archetype | Domains (instances) | Motion | Memory is about | Lineage |
|---|---|---|---|---|
| **Knowledge-work** | Guild, Curator | Convergent (toward decisions) | Decisions, rationale, open questions, evolving understanding of a body of work | README + decision log; GUILD_CHARTER |
| **Learning** | German, Portuguese | Accretive (toward mastery) | Mastery, error patterns, vocabulary, concept relationships over an evolving skill | "What I know about this learner" profile |
| **Exploration** | (new) Tech / physical AI / health / biology / grounding philosophies | Divergent (outward, following threads) | Open threads, emerging positions, and the links between them | Commonplace book; Luhmann's Zettelkasten |

### On the exploration archetype (the new one)
Robert described it as *"like Curator but not curated."* Curator narrows the firehose toward signal (convergent); exploration wanders outward across fields without knowing where threads go (divergent). Same raw material, opposite motion. Its memory unit is the **thread** (a live line of inquiry), the **position** (a tentative hardened conclusion), and the **link** (where physical AI brushes against biology brushes against a venture idea). The value lives in the connections, not the entries.

**We already built its machinery — in Curator.** Curator's "Thinking Record architecture (threads as discrete intellectual episodes with immutable motivations)" and its thread lifecycle (active/expired/closed/archived) is *exactly* what exploration needs. Exploration is **divergent Curator**: borrow the thread machinery, drop the scoring/curation. Cheap integration hiding in plain sight.

**It may be the heart of the original vision.** The founding description — *"a structured intellectual diary enabling future mining of thinking against content against new world events"* — is closer to exploration than to anything else built so far. German/Portuguese are skills; Guild is the workshop; Curator is the world filtered. Exploration is the diary. Robert may be circling back to what minimoi was meant to be.

### Schema consequence
- German and Portuguese **share a learning schema** (siblings — same logic, different content, exactly like the source-variety cap).
- Guild and Curator share a knowledge-work schema.
- Exploration gets its own (thread/position/link).
- **Three schemas, three archetypes.** Don't force one across all; don't build a bespoke one per domain.

### Spin-out is a free consequence
Because a domain is a clean archetype instance with per-user data isolation, **breaking a domain off is just deploying the archetype with a different tenant set.** The language domains becoming their own commercial product doesn't require a refactor — you instance the learning archetype for paying users against separate data. Generalizing *now* is what keeps spin-out cheap *later*. (Inverts the usual intuition: generality is the thing that preserves options, not a future bolt-on.)

---

## 4. Instances, tenants, and portability (the generalizability architecture)

Two productization models are live, and they pull differently:

- **Hand-a-base-to-your-daughter** (near-term, the one to design against): an instance must **fork** — base setup + generic prepopulation, handed off, grown independently, no further tether. Forking is stronger than isolation: isolation means you can't see each other's data; forking means her instance can *leave* and keep running.
- **Enterprise umbrella** (proof, not build): instances that are **separate but federated** — autonomous at the leaf, aggregated at the root. Each team or individual has a minimoi; the org keeps them under one umbrella.

### Local / model-agnostic is the *enabling condition* for portability
This reframes a principle that's been stated as a cost/independence argument. If an instance is coupled to a vendor's LLM, you can't hand it to your daughter without handing over your API account, and you can't drop it into an enterprise that mandates its own/on-prem models. **An instance that can run on any model is an instance that can run anywhere, for anyone.** Model-agnosticism and generalizability are the same principle from two sides. Portability is mostly the *absence of hidden couplings*: no foreign keys into Robert's data, no config that assumes Robert's EC2, no model endpoint hardcoded to Robert's keys.

### The layered model

| Layer | What it holds | Scope | Flows |
|---|---|---|---|
| **Leaf instance** | per-user content + per-instance intent | one person | self-contained; can fork & leave |
| **Seed / template** | empty archetypes + generic prepopulation (incl. seed personas, §5) | what a new leaf is born from | one-way: seeds a leaf at birth, then detaches |
| **Shared product knowledge** | cross-tenant archetype IP; optionally org-curated domain knowledge | all tenants | pushed *down*; never carries personal content *up* |
| **Umbrella** (enterprise only) | aggregation + governance across leaves | an organization | by **synthesized rollup**, with an explicit read-boundary on leaf content |

### The data membrane (was three-way, now four-way)
**per-user content / per-instance intent / shared product knowledge / umbrella rollups.** The enforcing mechanism already exists: per-user JSON keyed by `auth.users.id` (`data/{domain}/{feature}/user_{id}.json`). That pattern was built for multi-user isolation; it is *also* the generalizability boundary. Same wall, now several purposes. The hard, genuinely-new boundary is the umbrella read-line: **can the org read leaf content, or only synthesized rollups?** That's a governance/privacy decision, not just technical — and it directly echoes the SEI artifact vault (transcript/field-note split + sensitivity routing): "what stays raw at the leaf vs. what gets promoted upward synthesized." The umbrella is that vault logic applied to instances instead of documents.

### The discipline that keeps this from sprawling
**Generalize the architecture, instantiate for one.** Build the archetype properly (clean schema, per-user keys, no Robert-isms in the shape, no vendor lock), but only ever populate the single tenant that exists — Robert. **Design the seams, defer the machinery.** Do NOT build fork tooling, seed-management, tenant signup, onboarding wizards, or umbrella aggregation until a second real person or org needs them. This is "activate-when-data-volume-demands" applied to multi-tenancy: the seam is cheap, the machinery is not. The federation story is something you can *tell* (it's part of the enterprise-positioning thesis) without *building* it at the leaf stage you're in.

---

## 5. Seed personas — the instance bootstrap

The cold-start problem (a fresh instance is empty and useless on day one) is solved with a mechanism already proven: personas. But these are **seed personas** — not "who you talk to inside a domain" (that's Conversas/Maria) but "who the instance is *for*" at birth. One seed persona instantiates across *all* empty domains at once: it gives Curator a starter source list, exploration a couple of starter threads, the learning domain a plausible goal.

### Why two, not one
One seed persona is indistinguishable from a default — it reads as "the app's opinion." **Two reveal the seam:** the new user sees the starting interests are a *choice*, not a baked-in worldview, which makes it obvious they can swap, blend, or discard. Two also forces genuine generality: if you can write two that both feel inoffensive and clearly different, the seam is clean; if you can only write one and it's suspiciously into telecom and Rio, you've caught a Robert-ism leaking into the product.

### Interests, never positions
"Generic and inoffensive" is doing real work — it's the seed equivalent of the evenhandedness and child-safety instincts. A seed persona traffics in **broad curiosity, not stances.** "Interested in how technology shapes daily life" seeds sources without taking a side; "curious about health and longevity" seeds threads without pushing an ideology. The moment a seed persona holds a *position* it stops being a neutral kit and starts telling the user what to think. **Interests are invitations; positions are impositions.** The user's own positions accrete later as *they* explore — which is the exploration archetype doing its job.

### Sketch (illustrative, not final)
- **"The generalist"** — broadly curious across tech, health/biology, how-things-work; light on geopolitics; no strong stances. Curator → a few broad, reputable, low-controversy sources (science explainers, general tech/health — not partisan outlets). Exploration → 2–3 open starter threads ("what's actually happening in physical AI," "what does the longevity research consensus look like"). Learning → empty or a neutral "pick a language."
- **"The builder/doer"** — tilts toward ventures, systems, practical making. Different sources, different threads, same inoffensive register.

Two clearly different flavors, neither of them Robert, both immediately useful, both obviously swappable.

### Persona ≠ intent layer
The persona is the **spark** — generic, shipped, identical for everyone who picks it. The intent layer (§2) is what it *becomes* once a real person uses it — personal, accreted, theirs. The persona seeds the intent layer on day one and then **gets out of the way.** It must not keep asserting itself after the user has their own direction. (The membrane at persona level: shipped-generic bootstraps grown-personal, then dissolves.)

### Consolidation
The seed persona **is the seed-richness dial.** A rich persona warms every domain at once; a thin one stays cold. Tune richness by tuning the persona, not domain-by-domain. This closes the per-archetype seed-richness fork from the design session into a single lever.

---

## 6. Capture-first — the real bottleneck

Robert's sharpest point: the technical design of memory is **secondary** to the discipline of *capturing* it. If sessions don't reliably land in a structured place, synthesis/recall/graphs build on sand. Grok honestly flagged itself as the weak link (no persistent file write).

- **Now: OpenClaw is the capture point** — consistent with the binding working model ("OpenClaw manages memory/files, no git"), not a new decision.
- **Later: Guild becomes the coordinator** (reminders, "has today's session been saved?", light orchestration). Build the backend so Guild can take over without reshaping storage; entry point can stay OpenClaw.

**Three capture pathways (accept all three):**
1. **Direct write** — Claude Code, OpenClaw. High reliability.
2. **Structured handoff block** — agent emits a tagged, timestamped block; Robert routes it (often via Claude.ai as the de facto hub). Medium.
3. **Manual curation** — what Robert does today with handoffs. Medium-high.

Unifying mechanism: **every meaningful session ends with a standardized handoff block** — domain tag, timestamp, summary, decisions, open questions. A *commit message for thinking*, analogous to the existing spec discipline (DoD + Commit). Any agent that can't write directly must *emit the block* for routing. The format being standard is what makes the imperfect pathways survivable.

**Strict prompting (the persona analogy):** enforce the capture rule like the Conversas persona prompts — a standing instruction every agent carries: *"At the end of any non-trivial session, either write the handoff block to the correct domain location, or output it clearly for routing. Do not end without doing one."*

**Success criteria (what "useful" means):** this is a personal system, so the eval is not an apparatus. Two tests: (1) does starting a new agent conversation get easier and better? (2) does it make Robert and the agents spend more time thinking about *what we built and why*, and less re-deriving lost context? Aim for useful, not complete.

---

## 7. The graph question, threaded precisely

Grok kept reintroducing Neo4j via "design the schema for projection now or face a painful migration." There is a real line:

- **Allowed now (costs nothing, just hygiene):** stable IDs on entities, explicit *typed* relationships, insights/positions referencing concepts by ID not loose text. Good data modeling. Makes future projection mechanical without paying any ops/query/complexity cost today.
- **Deferred (infrastructure ahead of data):** standing up Neo4j, embeddings on nodes, Mem0/LangChain/Zep as the owning layer. These wait for demonstrated pain (context-window pressure from loading whole memory files; genuine many-hop relationship needs). Existing *activate-when-data-volume-demands* trigger, unchanged.

**Structure the JSON for graph-projectability; do not build the graph.** JSON stays source of truth; any future Neo4j is a rebuildable projection — consistent with JSON-first. Door stays open as a *build*, never a *rewrite*.

**Special note on exploration:** it is the most graph-shaped archetype — its whole value is cross-pollination/links, which is what Neo4j is *for*. It will likely be the domain that eventually justifies the graph first. But it is also the newest, lowest-volume, most incipient — so it still doesn't earn the infrastructure today. It is the best stress-test of the "hygiene now, graph later" rule under temptation: give its threads/positions stable IDs and typed cross-references from day one so it projects cleanly when the graph finally earns its place.

Neo4j + Postgres are already provisioned and idle. Idle infrastructure we grow into is fine; infrastructure we shape the whole design around prematurely is not.

---

## 8. Lessons that must bind this design (from project history)

The new memory system is *the same category of thing* that caused real incidents:
- An **API key was once exposed in memory files** (rotated).
- An **overnight API burn** came from `MEMORY.md` exceeding the bootstrap limit.
- A **billing hard stop** hit the xAI account.

These were *memory/capture* incidents — the reason "spend-follows-attention" and cost gates exist as principles at all. Binding constraints:

1. **Secret hygiene** — memory/session files must never carry credentials. Capture protocol strips or refuses secrets. (Keys leak into memory files; we have proof.)
2. **Cost gates on auto-loading** — never auto-inject large memory into every prompt. Intent layer is small by design (loadable); session transcripts are *not* loaded wholesale — synthesized down first. This is the real reason the two-layer split (§2) is load-bearing, not just tidy.
3. **JSON-first** — JSON is source of truth; Postgres/Neo4j are rebuildable projections.
4. **Design before build** — this document, and the iteration on it, *is* that step.

---

## 9. What we got wrong (asked for explicitly)

1. **Infrastructure-first.** Grok's original Phase 1 (Neo4j + Mem0 + embeddings "even if intelligence is basic") was premature. Complexity tax for grep-able questions.
2. **Conflating storage with the goal.** The real goal is continuity and intent — a synthesis problem, not a storage one.
3. **Underweighting capture discipline.** The hardest, most important part is reliably getting sessions into a structured place.
4. **Not separating session from intent.** Mixing them breaks both management and the cost gate.
5. **(Addition) Scoping it as a language feature.** It is the project's core promise; treating it as a sub-feature delayed seeing what it is.
6. **(Addition) Forgetting our own incident history.** The key leak and overnight burn were memory-file failures; carry the lessons forward as constraints (§8).
7. **Over-indexing on automation too soon.** Sophisticated synthesis before reliable capture is cart-before-horse.
8. **(New, watch for it) Premature generalization** — the cousin of premature infrastructure. Designing for hypothetical other users' hypothetical domains would abstract what doesn't need abstracting and slow the tool that has to make Robert better *next week*. Guard: *generalize the architecture, instantiate for one.*

---

## 10. Open questions to play with (the forks)

1. **Intent-memory granularity.** Free-form narrative vs structured fields (decisions / open questions / evolving understanding / rationale)? Lean: light structure for knowledge-work, looser for learning.
2. **Synthesis cadence/trigger.** Nightly vs weekly vs ad-hoc? Per-domain vs one pass split by domain? Cumulative (references prior syntheses) or last-7-days?
3. **What synthesis produces.** Narrative, structured fields, or both? Different per archetype?
4. **Cross-domain awareness.** Strictly isolated, or a thin link when a Guild decision affects how Portuguese is built? Lean: isolated now, ID hygiene leaves the option open.
5. **JSON exit ramp.** What concrete signal flips us to a Neo4j projection? Name the trigger so it's a decision, not a drift. (Exploration most likely to hit it first.)
6. **Handoff block format.** Highest-value thing to nail first — everything downstream consumes it. Fields, tags, mandatory vs optional?
7. **(New) Exploration lifecycle — staging area or permanent home?** Exploration is *upstream* of the others: a wandering thought can harden into a Guild build, a Curator thread, or a venture. Does a matured position *move out* into another domain, or *stay* in exploration and get referenced? Decides whether the archetype is a staging area or a home — and shapes its schema.
8. **(New) Umbrella knowledge-flow direction.** Does the org push shared knowledge *down* to instances, pull synthesized insight *up*, or both? And the hard one: can the umbrella read leaf content, or only rollups? Governance/privacy, not just technical.
9. **(New) Seed richness.** How generic is the seed? Near-empty (maximally general, cold-starts hard) vs richly prepopulated (warm, but carries opinions). Likely different per archetype — learning wants rich scaffolding; exploration probably wants near-empty so it doesn't impose someone else's curiosity. (The seed persona is the dial — §5.)
10. **(New) Two seed personas — complementary or just different?** Should the pair deliberately *cover different ground* (so between them they hint at the full range) or simply be *distinct flavors*? Affects how well the seam reads.

---

## 11. When we do start — a deliberately small first step

Not now. So the on-ramp is visible without committing to a warehouse-first build:

- **Step 1 — Capture protocol + handoff block format.** The standard, domain-tagged, timestamped block every agent emits. Pure discipline, zero infrastructure. (Q10.6 is the gate.)
- **Step 2 — Per-domain intent docs.** One small living "charter/direction" doc per domain (generalize GUILD_CHARTER). Read at conversation start, revised at end. The part Robert needs most and cheapest to stand up.
- **Step 3 — Session capture to OpenClaw**, domain-tagged folders (`memory/{domain}/...`), append-only.
- **Step 4 — Synthesis** (weekly, per domain) — only after 1–3 are reliable.
- **Step N — Graph projection** — only on a named pain trigger (Q10.5). Maybe never.

Throughout, hold the two new disciplines alongside the old: **generalize the architecture, instantiate for one** and **design the seams, defer the machinery.** The seed persona (§5) is a Step-1/2 concern (it's just content + the membrane); fork tooling, umbrella aggregation, and tenant management are deferred until a real second tenant exists. Each step is useful on its own; none blocks on the next. The graph is the last thing, not the first.

---

## Working model (cross-agent footer)

Robert decides/approves. Claude.ai designs/specs (no git). Claude Code builds/git/deploy. OpenClaw memory/files (never git) — **and is the memory entry point for this system.** Grok reviews. This is a design document; it becomes a spec (with DoD + Commit) only when we choose to start. Good target for a Grok review pass during the iteration window.

---

*Conceptual design v2 · 2026-06-29 · iterate, don't build · pairs with Grok "Deep Conceptual Think" PDF and design_note_memory_layer_grok_eval_2026-06-28*