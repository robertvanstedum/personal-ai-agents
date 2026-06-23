# Handoff — Domain Product Descriptions for GitHub
*2026-06-16 · Claude Code research → Claude.ai writing*

---

## The ask

Write a focused product description for each of the three active domains:
**Curator**, **Mein Deutsch**, and **Guild**.

Destination: GitHub — one doc per domain, likely `docs/CURATOR.md`,
`docs/MEIN_DEUTSCH.md` (or `docs/GERMAN.md` update), `docs/GUILD.md` update.

**Tone target:** More focused than the README portfolio pitch. Less technical
than an architecture doc. Reads like a product page for someone evaluating
whether this is interesting — a peer product manager or engineer, not a
developer looking for API docs.

**Not:** a build log, a roadmap, a spec, a README.

---

## What already exists — per domain

### Curator

| File | What it covers | Usable? |
|---|---|---|
| `docs/curator-research-concept-v2.md` | Philosophy, user flows (Scan→Dive→Topic states), Leanings model, decision support framing | Strong foundation — but concept-era, pre-current IA |
| `docs/curator-research-concept-v1.md` | Earlier framing of two-tier gathering/testing model | Background context only |
| `README.md` (Curator section) | ~700 candidate scoring, cost baseline, daily use since Feb 2026 | Good snapshot, too brief for a standalone doc |

**Current nav:** `DAILY · READING ROOM · SCANS & DIVES · LEANINGS · ARCHIVE · DESK`

**What's missing:** A current description that matches actual v1.1 IA. The concept
docs describe a future state that's partially built. No single doc says clearly:
*this is what Curator does, this is who it's for, this is why it's built this way.*

---

### Mein Deutsch

| File | What it covers | Usable? |
|---|---|---|
| `docs/GERMAN.md` | Full feature overview: personas, drills, Anki, Telegram, scaffold. ~6,400 words | Most complete — but predates v1.1 voice/KI-Sitzung features |
| `_NewDomains/DOMAIN_SPEC_language_learning.md` | Vision doc: personal AI tutor, language-agnostic architecture, B1 in 12 months | Good framing, pre-build |
| GitHub release `mein-deutsch-v1.1` | Just published — cross-workflow learning loop, voice, real sessions, analysieren | Exact and current — good anchor |

**What's missing:** `docs/GERMAN.md` is long and pre-v1.1. A tighter current doc
that reflects: KI-Personas + real sessions sharing one pipeline, the
Lesen/Gespräche/Schreiben/Wörter/Archiv structure, and the Vienna testing context.

---

### Guild

| File | What it covers | Usable? |
|---|---|---|
| `GUILD.md` (root) | Charter: guild metaphor, roles, working protocols, design principles | Vision foundation |
| `docs/GUILD.md` | Full system design: CoS model, three agent layers, intelligence loops, interface pages | ~15k words — thorough but sprawling |
| `docs/GUILD_AGENTS_DESIGN.md` | Agent specs (Operations, CoS, Design/Dev) | Technical |

**Current state:** Operations agent running under launchd. CoS chat on rvsopenbot.
Phase 3 complete. Intelligence loops (Phase 4) planned.

**What's missing:** A doc that explains Guild as a *concept* before explaining its
mechanics. Why does an agent system need a "Guild"? What is the Chief of Staff
actually doing? What does it look like to work with the Guild day to day?
The existing docs are thorough on mechanics but thin on the *why it's designed
this way* angle.

---

## Suggested output from Claude.ai

For each domain, produce a product description draft (~500–900 words each)
structured roughly as:

1. **What it is** — one crisp paragraph
2. **What it does** — the core loop or workflow, from the user's perspective
3. **Why it's built this way** — the design reasoning, not the architecture
4. **Current state** — what's live, what's version-tagged, what's next

These drafts will be reviewed by Robert, then handed back to Claude Code as
specs to write/update the actual `docs/` files.

---

## Source material to read before writing

Priority order:
1. `docs/curator-research-concept-v2.md` — Curator philosophy
2. `docs/GERMAN.md` — German domain features
3. `GUILD.md` (root) — Guild charter
4. `docs/GUILD.md` — Guild system design (skim for current state)
5. GitHub release notes `mein-deutsch-v1.1` (published 2026-06-16) — exact current feature list
6. `README.md` (root) — overall platform context and voice

**Also useful:** The landing page blurbs at `static/public/index.html` show the
current approved one-liner for each domain.

---

## Current approved one-liners (from landing page, locked)

**Curator v1.1:**
> ~700 candidates scored daily by a reasoning model against your learned profile —
> counterpoints by design, not an echo chamber. In daily use since February 2026.

**Mein Deutsch v1.1:**
> German practice built around your own life. Build your own KI-Personas and scenes,
> manage reading lists tuned to your interests, and practice with KI-Personas or real
> people — transcripts flow through the same feedback pipeline either way. Mistakes
> become drills. Vienna-tested.

**Guild v0.9:**
> Where the agents and I work together to get better at our craft. From ideas through
> specs, build, and operations. Guild is intentionally cross-domain. A Chief of Staff
> role coordinates the agents while keeping my real goals and private concerns in view.

---

*Deliverable from this handoff: three draft product descriptions. Robert reviews.
New spec per domain → Claude Code writes/updates the docs/ files.*
