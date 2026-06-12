# Plan — CoS Soul v0.1 (Narrow Scope)
*mini-moi · Guild · CoS Identity*
*Authored: 2026-06-11 19:15 CDT — Claude.ai + Robert*
*Status: ready to commit — local git tonight, push to main when ready*
*Supersedes scope of: GUILD_CoS_Identity_Founding_2026-06-11.md (kept — see "What's on the bookshelf")*

---

## What this is

A narrow, practical first step toward CoS feeling like a working partner rather
than a task master — without the larger commitments of the founding document.

One file. One injection point. Markdown, not JSON — this is meant to be read
more like a short personal credo than structured config.

**`cos_soul.md`** — included separately in this handoff, ready to commit as-is.

The content is intentionally short (~150 words). Not a persona built from
reference figures, not a biography of Robert — closer to a constitution.
What CoS is for, how it holds itself, what it isn't. The kind of thing that
shapes tone without being recited.

---

## Where it lives

```
domains/guild/config/cos_soul.md
```

Alongside `cos_context.json` (Robert's goals/config) but distinct in kind —
`cos_context.json` is data CoS reads about the world; `cos_soul.md` is closer
to CoS's own sense of itself.

**Visibility:** can be public from the start. Unlike `principal_profile.json`
(bookshelved — see below), this file contains nothing about Robert specifically.
It's safe to commit to public GitHub as part of `docs/` or alongside the config,
and could be referenced in `docs/GUILD.md` as part of the CoS description.

---

## Injection

**Scope: `/chat` only.**

Added to the CoS `/chat` system prompt, alongside `cos_context.json`. Not
applied to:
- Intelligence loops (Loop A career scoring, Loop B/C/D scouting) — these stay
  accuracy-first
- Operations (Tier 1-4 actions) — mechanical, no identity needed
- Loop F (domain health) — deferred until the soul's effect on `/chat` is
  understood

This mirrors the research finding from earlier tonight: persona-style context
helps when it's tightly aligned with the task, and can hurt accuracy-sensitive
tasks when it isn't. `/chat` is the one surface where "how this is said" matters
as much as "what is said" — so it's the right (and only) place to start.

**Implementation:** one addition to the CoS `/chat` endpoint — read
`cos_soul.md`, include its content in the system prompt alongside the existing
context. No schema change, no new dependencies.

---

## Versioning

`cos_soul.md` carries a version header (`v0.1`) and an explicit closing note:
*"This file is meant to be revised. If it stops feeling true, change it."*

No formal changelog needed at this scale — the file is short enough that git
history is the changelog. If it grows significantly, add a changelog section.

---

## Measurement — informal, v0.1

No formal test battery for this narrow version. Over the next 1-2 weeks:

- Robert notices, in passing, whether `/chat` responses feel different —
  logged informally (a line in `cos_memory.md` or just a mental note)
- If something stands out — good or bad — note it
- No dedicated test sessions, no rubric

This is deliberately lightweight. The founding document's full measurement
plan (4 tests, 2-3 week structured period) applies if and when the more
ambitious version (principal profile, composite character) gets built.

---

## What's on the bookshelf

From `GUILD_CoS_Identity_Founding_2026-06-11.md` — not abandoned, just not now:

- **`principal_profile.json`** — the journal → extract → mask workflow for
  Robert's wider context (career arc, health, family, the longer journey).
  Stays private when/if built. Deferred because: highest effort, highest risk
  of the "irrelevant detail degrades accuracy" failure mode if done before the
  soul's baseline effect is understood.

- **Composite character framework** (Rahm Emanuel / RFK Jr. / Elon Musk traits) —
  the richer personality-from-reference-figures approach. The soul.md is a
  lighter, more essential alternative to this — if the soul doesn't produce a
  noticeable shift, the composite approach is the next thing to try, not
  before.

- **Loop F cross-domain integration** — applying any identity/context layer
  to the domain-health loop. Deferred until `/chat` results are understood.

- **The 4-test measurement plan** — full rigor, for when there's more surface
  area to measure.

The founding document stays in `_working/` (or wherever it currently lives) as
the reference for "what's next if this works."

---

## Commit (tonight — local minimum)

```bash
mkdir -p domains/guild/config
cp cos_soul.md domains/guild/config/cos_soul.md

git add domains/guild/config/cos_soul.md \
        _working/GUILD_CoS_Identity_Founding_2026-06-11.md \
        _working/plan_cos_soul_v0.1_2026-06-11.md

git commit -m "feat: add CoS soul.md v0.1 — narrow identity scope for /chat

Bookshelves the fuller composite-character + principal-profile approach
from the founding doc. This is a single short markdown file (~150 words),
injected into /chat only, public-safe (no personal data). Informal
1-2 week read on whether it changes anything before going further."
```

Push to main when ready — no rush, local commit tonight protects the work.

---

## Tomorrow morning — when picking this back up

1. Confirm `cos_soul.md` content still feels right (it's meant to be revised —
   read it fresh and adjust if needed)
2. Small code change: CoS `/chat` endpoint reads and includes `cos_soul.md`
3. Optional: one line in `docs/GUILD.md` under the CoS section, noting the
   soul.md exists and what it's for
4. Then: back to practical Guild build — Career two-page (build plan ready),
   pipeline schema rename (prerequisite)

---

*CoS Soul v0.1 — narrow plan · Guild · 2026-06-11*
*Founding document preserved for the more ambitious direction, unchanged.*
