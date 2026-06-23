# Plan — Domain Product Descriptions: Content Writing Session
*Created: 2026-06-16 — Claude.ai*
*Status: Queued — return to this when ready to write*
*Depends on: `handoff_domain_product_descriptions_2026-06-16.md` (Claude Code research, complete)*

---

## What this is

Claude Code has done the source research across all three domains and produced
a clear gap analysis. The writing work is queued here for a dedicated Claude.ai
session. Robert reviews drafts, then a Claude Code spec per domain writes/updates
the actual `docs/` files.

**Not a build spec yet.** This becomes three build specs after the writing session.

---

## The three-session output target

One product description per domain, ~500–900 words each, structured:

1. **What it is** — one crisp paragraph
2. **What it does** — the core loop or workflow, user's perspective
3. **Why it's built this way** — design reasoning, not architecture
4. **Current state** — what's live, what's version-tagged, what's next

Destination files (for Claude Code after review):
- `docs/CURATOR.md` (new or replace)
- `docs/MEIN_DEUTSCH.md` (tighter replacement for the 6,400-word `docs/GERMAN.md`)
- `docs/GUILD.md` (concept-first rewrite of the sprawling mechanics doc)

---

## Per-domain brief — what to write toward

### Curator

**Source anchor:** `docs/curator-research-concept-v2.md` for philosophy,
README for current snapshot, landing blurb for locked voice.

**The gap:** No single doc says clearly what Curator does, who it's for, and
why it's built this way. Concept docs describe a partially-built future state.
Current nav (`DAILY · READING ROOM · SCANS & DIVES · LEANINGS · ARCHIVE · DESK`)
gives the structure but not the story.

**What the description needs to land:**
- The signal-vs-noise framing: ~700 candidates scored daily, not a feed
- The learned profile: it scores against *your* leanings, not a generic ranking
- The counterpoint design: not an echo chamber by architecture, not by intention
- The two-agent Deeper Dive (Synthesizer + Challenger, CIA Devil's Advocacy)
- Why source architecture matters: trust tiers, age decay, X post hard cap
- In daily use since February 2026 — this is real, not a demo

**Locked one-liner to stay consistent with:**
> ~700 candidates scored daily by a reasoning model against your learned
> profile — counterpoints by design, not an echo chamber. In daily use since
> February 2026.

**Questions to resolve in writing session:**
- Is "geopolitics and finance" still the right scope description, or has it
  broadened?
- How much of the source architecture is worth surfacing vs. keeping internal?
- Does the Research Intelligence / Deeper Dive feature warrant its own section
  or fold into "What it does"?

---

### Mein Deutsch

**Source anchor:** GitHub release `mein-deutsch-v1.1` (just published —
exact and current). `docs/GERMAN.md` for feature depth. Locked landing blurb.

**The gap:** `docs/GERMAN.md` is 6,400 words and predates v1.1 voice/KI-Sitzung.
Need a tighter current doc that reflects the cross-workflow pipeline and the
Lesen/Gespräche/Schreiben/Wörter/Archiv structure.

**What the description needs to land:**
- Build your own personas and scenes (not pre-packaged content)
- KI-Personas and real people share one feedback pipeline — the architecture
  is the differentiator
- The learning loop: practice offline → apply with real people → feed
  transcripts back → come back better prepared
- Daily-refreshed reading lists tuned to your own interests
- Mistakes become drills automatically — nothing to configure
- Vienna-tested — authenticity signal, not a marketing line

**Locked one-liner to stay consistent with:**
> German practice built around your own life. Build your own KI-Personas and
> scenes, manage reading lists tuned to your interests, and practice with
> KI-Personas or real people — transcripts flow through the same feedback
> pipeline either way. Mistakes become drills. Vienna-tested.

**Note:** Mein Deutsch product description draft already exists from 2026-06-15
session (`docs_mein_deutsch_product_description_2026-06-15.md`). Review and
tighten against the locked blurb and v1.1 release notes before treating as
final. May need only light editing rather than a full rewrite.

**Questions to resolve in writing session:**
- Does the 2026-06-15 draft hold up against the v1.1 release notes, or does
  it need structural changes?
- How much of the tab structure (Lesen/Gespräche/Schreiben/Wörter/Archiv)
  surfaces in the product description vs. staying in feature docs?

---

### Guild

**Source anchor:** `GUILD.md` (root) for charter and metaphor. `docs/GUILD.md`
for current state (skim — it's 15k words). Landing blurb for locked voice.

**The gap:** Existing docs are thorough on mechanics but thin on the *why it's
designed this way* angle. Need concept-first: why does an agent system need a
"Guild"? What is the Chief of Staff actually doing day to day? What does
working with the Guild look like?

**What the description needs to land:**
- The guild metaphor: craftspeople improving at their work together, not just
  task execution
- The Chief of Staff role: coordinator with full context, not a domain agent —
  keeps Robert's real goals and private concerns in view
- Cross-domain by design: Guild sees across Curator, German, Career, and
  future domains — that's the point
- Current state: Operations agent running under launchd, CoS chat on
  rvsopenbot, Phase 3 complete
- The "get better at our craft" principle — Guild improves the system itself,
  not just runs it

**Locked one-liner to stay consistent with:**
> Where the agents and I work together to get better at our craft. From ideas
> through specs, build, and operations. Guild is intentionally cross-domain.
> A Chief of Staff role coordinates the agents while keeping my real goals
> and private concerns in view.

**Questions to resolve in writing session:**
- Is v0.9 still the right version marker, or has enough shipped to call it v1.0?
- How much of the cabinet model (Chief of Staff, Design-Build, Operations,
  New Ventures) surfaces publicly vs. stays internal?
- Does Guild need a "who this is for" section — just Robert now, but designed
  to scale?
- Is the guest access / CoS nudge system far enough along to mention in
  current state?

---

## Session format — how to run the writing session

**Open with:** paste this doc + `handoff_domain_product_descriptions_2026-06-16.md`

**Work order:** Curator first (no existing draft, clearest scope). Mein Deutsch
second (existing draft to review, lightest lift). Guild third (most to figure
out on the public-facing story).

**Per domain:**
1. Claude.ai drafts ~500–900 words using source material briefs above
2. Robert reviews inline — line edits or directional notes
3. Lock the draft
4. Claude.ai produces a Claude Code spec to write/update the `docs/` file

**One domain per pass** — don't try to do all three in one session.

---

## Build specs to produce (after writing session)

| Spec | Output file | Depends on |
|------|-------------|------------|
| `spec_curator_product_description.md` | `docs/CURATOR.md` | Curator draft locked |
| `spec_mein_deutsch_product_description.md` | `docs/MEIN_DEUTSCH.md` | Mein Deutsch draft locked |
| `spec_guild_product_description.md` | `docs/GUILD.md` | Guild draft locked |

Each spec includes: the locked draft as content, target file path, DoD
(renders correctly in GitHub, consistent with landing blurb, replaces or
supplements named existing file), and Commit message.

---

## Files to have ready for the writing session

| File | Where |
|------|-------|
| `handoff_domain_product_descriptions_2026-06-16.md` | This session's uploads |
| `docs_mein_deutsch_product_description_2026-06-15.md` | Last session's outputs |
| This planning doc | This session's outputs |
| Current landing page screenshot | Take fresh screenshot before session |

---

*Planning Doc · 2026-06-16 · Claude.ai*
*Return to this to open the Curator/Guild/Mein Deutsch writing session*
