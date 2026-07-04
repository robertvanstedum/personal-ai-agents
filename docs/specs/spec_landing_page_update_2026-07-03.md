# Spec: minimoi.ai Landing Page Update
*Created: 2026-07-03 · Claude.ai (design) → Claude Code (build)*

---

## Intent

The minimoi.ai landing page currently has two problems. First, the domain
blurbs (Curator, Mein Deutsch, Guild) are out of date — they don't reflect
the current state of the system. Second, there is no framing for visitors
or guests who land there without context. Someone invited by Robert arrives
and compares what they see to a consumer product. That's the wrong frame.

This spec covers both: updated domain blurbs (once copy is finalized in a
separate design session) and a new visitor/guest framing section that sets
the right expectations before anyone clicks anything.

The landing page is the first thing every user sees — owner, family,
guest, and anyone Robert shares the URL with. It should tell the truth
about what mini-moi is.

---

## Source Material (for design session and Claude Code reference)

The four sources below are the canonical reference for the landing page
update. All copy decisions should be consistent across them. Version
numbers, domain names, and positioning language must align.

### Current landing page text (as of 2026-07-03)

**Hero:**
> Your context. Your goals. Your signal.

**What this is:**
> LLMs have the world's knowledge. They don't have your intent, your
> goals, your history, your risk tolerance.
>
> mini-moi builds toward specific intelligence in your context. It works
> with cloud LLMs, stores memory locally, runs a local model, and acts
> together with you.
>
> Focused on specific domains first. Built to grow together over time.

**What's running (current blurbs — to be updated):**
- Curator v1.1 — Geopolitics and finance briefing...
- Mein Deutsch v1.1 — German practice built around your own life...
- Guild v0.9 — Where the agents and I work together...

**Note: Meu Português is absent from the current landing page.**
It is a live domain in daily use and must be added.

---

### Resume — AI & Personal Projects section (as of 2026-07-03)

> Designed and built a local-first, model-agnostic personal AI agent
> system centered on my own interests and goals. Running in daily
> production on AWS EC2 since June 2026, with multi-user support and
> multiple domains.
>
> • Curator — Daily geopolitics and finance briefing from ~700 article
>   candidates, scored against a learned personal profile. Delivered via
>   web portal and Telegram. Closed learning loop with 400+ feedback
>   signals. Model-agnostic: xAI Grok for scoring, Anthropic Claude for
>   synthesis, local Ollama as fallback.
>
> • Mein Deutsch + Meu Português — Two language domains built to close
>   the gap between understanding and speaking in real conversations.
>   Custom AI personas and scenes enable unscripted voice practice. AI
>   analysis turns session errors into targeted drills. Multi-user
>   architecture supports family accounts with role-based access.
>   Both in daily use.
>
> • Guild + Infrastructure — Agent coordination layer with structured
>   decision records and build queue discipline. Full stack on AWS EC2:
>   Docker containerization, GitHub Actions CI/CD, Elastic IP, SSL.
>   Extending solution to include long-term learning and memory.

---

### GitHub About (as of 2026-07-03)

> A personal AI agent platform — local-first, model-agnostic. Three
> domains in production on AWS: Curator (daily intelligence briefings),
> Mein Deutsch (German language immersion), and Guild (agent
> coordination). Guild v1.0 released June 2026.

---

### README — key sections (as of 2026-07-03)

**Opening (strongest statement of what this is):**
> LLMs have the world's knowledge. They don't have your intent, your
> goals, your history, your risk tolerance. mini-moi builds toward that
> gap: specific intelligence in your context. Local memory,
> model-agnostic pipeline, swappable LLMs. Your context stays with you.
> Switch models, switch providers, go offline. The agent still knows you.

**Domain descriptions (README versions):**
> Curator v1.2 — Geopolitics and finance briefing. ~700 RSS and X
> candidates scored daily by a reasoning model with your learned profile.
> Top 20 to the web portal, top 10 to Telegram at 7 AM. In daily use
> since February 2026.
>
> Mein Deutsch v1.0 — German language coaching pipeline. Vienna-tested.
> Lesen, Gespräche, Schreiben, Wörter. Anki cards earned from real
> friction, not passive review.
>
> Guild — Personal operating system. Chief of Staff model with autonomous
> agents, a career and work pipeline, and a daily executive briefing
> across all domains.

**"What This Is Really About" (README — strongest vision statement):**
> The cloud LLMs have the world's knowledge. That problem is largely
> solved. The hard part — the part that actually matters for real
> decisions — is acting in your specific situation. Your history. Your
> goals. Your risk tolerance. Your team's context and motivation. General
> intelligence is widely available now. Specific intelligence, the kind
> that knows you and acts for you, isn't. That's what this builds toward.

---

### Cross-source inconsistencies to resolve in design session

| Item | Landing | Resume | GitHub | README |
|---|---|---|---|---|
| Curator version | v1.1 | — | — | v1.2 |
| Mein Deutsch version | v1.1 | — | — | v1.0 |
| Guild version | v0.9 | — | v1.0 | — |
| Meu Português | ❌ absent | ✅ present | ❌ absent | ❌ absent |
| Portuguese domain name | — | Meu Português | — | — |

Version numbers and domain presence must be consistent across all four
before the landing page update ships. The design session should lock
these decisions. Claude Code should not invent version numbers — wait
for Robert to confirm.

---



### What it is
A short honest statement about what mini-moi is and isn't. Appears on the
landing page for everyone — not just guests, not hidden behind login. Sets
the comparison frame before the user sees anything else.

### Placement
Below the hero/header, above the domain cards. Short, not a wall of text.
Visually distinct from the domain blurbs — quieter, more editorial in tone.

### Draft copy (for Robert to refine)

> **mini-moi is a personal AI system — built for Robert and shared with
> a small circle.**
>
> You're not using a product. You're a guest in a working system built
> around one person's interests, goals, and way of working. Some things
> will be unfamiliar. Some are still rough. That's intentional — this
> is a living system, not a finished app.
>
> If you're here, Robert invited you.

### Design notes
- Tone: direct, honest, warm but not apologetic
- Not a disclaimer — a frame
- Should not look like a legal notice or a warning
- Muted styling — smaller text, slightly indented or set apart visually
  from the main content
- No CTA button needed — this is context, not a conversion moment

### What this replaces
Nothing is removed. This is a new section. It sits between the hero and
the existing domain cards.

---

## Part 2 — Domain Blurb Updates

### Status: PENDING DESIGN SESSION

The three domain blurbs (Curator, Mein Deutsch, Guild) need to be updated
to reflect the current state of the system. A separate design session with
Claude.ai is needed to write the final copy — the blurbs should map to
the GitHub README and Robert's resume, not be written from scratch here.

**Placeholder approach:** Claude Code should build the layout and structure
with the current blurbs in place. When the design session produces final
copy, it drops in as a content-only update — no code change needed.

### Current blurbs (from Telegram note 2026-07-02, Robert's draft)
These are Robert's working drafts — not final, pending the design session:

> **Curator v1.1** — Geopolitics and finance briefing. ~700 candidates
> scored daily by a reasoning model against your learned profile —
> counterpoints by design, not an echo chamber. In daily use since
> February 2026.

> **Mein Deutsch v1.1** — German practice built around your own life.
> Build your own KI-Personas and scenes, manage reading lists tuned to
> your interests, and practice with KI-Personas or real people —
> transcripts flow through the same feedback pipeline either way.
> Mistakes become drills. Vienna-tested.

> **Guild v0.9** — Where the agents and I work together to get better
> at our craft. From ideas through specs, build, and operations. Guild
> is intentionally cross-domain (Curator, German, and beyond). A Chief
> of Staff role coordinates the agents while keeping my real goals and
> private concerns in view.

These can go live now as an improvement over what's currently there.
They will be refined in the design session before final lock.

---

## Build order

1. **Part 1 first** — visitor framing section. Self-contained, copy is
   clear enough to build now, no dependency on the design session.
2. **Part 2 second** — swap in Robert's working draft blurbs now as an
   improvement. Final copy drops in later as content-only update.

Both parts can ship in one commit if Part 1 is straightforward.

---

## Definition of Done

**Part 1 — Visitor framing**
- [ ] Framing section appears on landing page below hero, above domain cards
- [ ] Renders correctly on mobile and desktop
- [ ] Styling is muted and distinct from domain blurbs (not a warning, not a CTA)
- [ ] Copy matches draft above (or Robert's refined version)
- [ ] Visible to all visitors — no login required to see it

**Part 2 — Domain blurbs**
- [ ] Three domain blurbs updated to Robert's working draft copy above
- [ ] Layout unchanged — blurb update is content only
- [ ] Flagged in code comment: "Copy pending final design session — update
      without code change when ready"

---

## Commit

```
feat: landing page update — visitor framing + domain blurb refresh

- New visitor/guest framing section: sets expectations before login
  ("personal system, not a product, you're here because Robert invited you")
- Domain blurbs updated to current working draft (Curator v1.1,
  Mein Deutsch v1.1, Guild v0.9)
- Blurb copy flagged for final update after design session
- Mobile and desktop validated

Closes #[ISSUE_NUMBER]
```

---

## Pending (not in this spec)

- Final domain blurb copy (design session with Claude.ai — to be scheduled)
- Meu Português blurb (domain is newer, needs its own blurb in the
  design session)
- Any structural layout changes to the landing page beyond the new section

---

*Spec · 2026-07-03 · build-ready for Part 1 + Part 2 working draft*
*Part 2 final copy pending design session*
