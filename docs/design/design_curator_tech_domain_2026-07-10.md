# Curator Tech Domain — High Level Approach
**File:** `docs/design/design_curator_tech_domain_2026-07-10.md`
**Status:** Idea → Design
**Date:** 2026-07-10
**Author:** Claude.ai design session

---

## Intent

Curator Tech is a second intelligence domain inside Curator — not a separate platform, not an academic reading list. Same philosophy as the existing Curator: decision context, not news, not entertainment.

**Existing Curator:** World signals — geopolitics, finance, technology as forces shaping the environment Robert operates in.

**Curator Tech:** Practitioner signals — what to build, what to adopt, where physical and biological technology is actually going, and what that means for mini-moi, career, and long-term thinking.

Both domains share the same core purpose: *get better together, for real-world decisions and actions.*

---

## What This Is Not

- Not a tutorial feed or learning curriculum
- Not AI news or vendor announcements
- Not academic research for its own sake
- Not "Democrats vs Republicans" equivalent in tech — not hype cycles, not stock plays

---

## Decision Context (what it informs)

Curator Tech exists to sharpen decisions across three areas:

**1. mini-moi roadmap decisions**
What memory architecture should we adopt? Is this agent framework worth evaluating? What's the trajectory of local model capability and when does it change what we build?

**2. Career and positioning decisions**
Where is physical AI actually creating value? What should a senior technology leader know and be able to speak to credibly in 2026-2027? Where are the 10-year bets worth making?

**3. Broader thinking and mental models**
What does mycorrhizal network research tell me about distributed intelligence? Where are living systems and artificial systems converging in ways that matter? What's real vs hype in robotics and embodied AI?

---

## Interest Areas (loose, not rigid categories)

Three broad areas — articles can span multiple, tags emerge from reading rather than being pre-defined:

**AI & Agents**
LLM internals, memory architectures, agent loops, local models, fine-tuning, context learning, frameworks worth evaluating. Emphasis on what actually works in production, not what's theoretically interesting.

**Living Systems**
Biology as computation — mycorrhizal networks, swarm intelligence, plant signaling, ecosystem dynamics. The parallel between natural and artificial intelligence. Relevant where it informs how we think about agent memory, distributed systems, and long-term learning.

**Physical World Tech**
Robotics, embodied AI, IoT at scale, edge computing, sensors and real-world data. Manufacturing, agriculture, infrastructure — where digital meets physical. More important long-term than selling digital products.

---

## Architecture Approach

**Inside Curator, not a new top-level domain.**
Curator already has the scoring engine, RSS pipeline, source management, category system, and briefing delivery. Curator Tech inherits all of this. Building a new domain from scratch would violate the reuse-and-extend principle.

**Interest tags, not fixed categories.**
Articles get tagged loosely. An article can span AI + Living Systems. Tags emerge from what gets read and saved, not from a predefined taxonomy. Flexibility upfront, structure emerges over time.

**Scoring philosophy — decision relevance, not topic match.**
The scoring prompt evaluates: *"Does this article contain signal that would change a thoughtful person's view on AI architecture choices, physical technology trajectories, or the intersection of living systems and computation — in a way that informs real decisions?"*

Recency weights higher than existing Curator (field moves fast). Depth weights higher (practitioner content over press releases). Vendor announcements weight lower.

**Separate briefing from existing Curator.**
Curator Tech delivers its own briefing — not mixed into the geopolitics/finance feed. Same delivery infrastructure (web portal + Telegram), separate view.

---

## Source Strategy

Sources to be defined in a follow-on design session. Starting point:
- ArXiv (cs.AI, cs.LG, q-bio selected categories — not all)
- Specific Substacks and newsletters (practitioner-focused, not hype)
- X/Twitter accounts (selected researchers and builders)
- Institutional blogs (Anthropic, xAI, DeepMind — filtered for signal)
- Physical tech publications (IEEE Spectrum, robotics-specific feeds)
- Living systems / biology publications (to be identified)

Source curation is as important as scoring. Wrong sources make good scoring irrelevant.

---

## Connection to mini-moi Vision

mini-moi is a process, not a product. It is learning by doing, with reinforcement over time — seeing history, evaluating decisions, improving future choices. Curator Tech is one more domain where that process applies:

Read → Score → Brief → Save → Feedback → Learn → Better decisions over time.

The corpus of saved and liked articles in Curator Tech becomes, over time, a record of what mattered and why. That record feeds the memory/intelligence layer (Phase 2) and eventually the fine-tuning corpus (Phase 3).

---

## Next Steps (when ready)

1. Source list design session — identify 15-25 sources across three areas
2. Scoring prompt design — calibrate against existing Curator scoring
3. Backend spec — RSS ingestion, tagging, scoring, separate briefing view
4. Build spec — Claude Code implements as Curator category extension
5. Run for 4-6 weeks — evaluate signal quality before deciding on standalone domain

---

## Open Question

**Standalone domain vs Curator category — revisit after 4-6 weeks of use.**
Start inside Curator (lower effort, inherits infrastructure). If the content volume, scoring needs, or UX diverge significantly from existing Curator, promote to standalone domain at that point. Don't pre-optimize.

---

*Design document · mini-moi · 2026-07-10 · Idea/Design phase*
*Source list and scoring design are the next required steps before build spec*
