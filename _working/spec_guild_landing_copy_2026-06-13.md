# Spec — Guild + Curator Landing Copy: minimoi.ai "What's Running"
*mini-moi · Guild*
*Created: 2026-06-13 — Claude.ai*
*For: Claude Code*

---

## What's changing

The Guild entry in minimoi.ai's "What's running" section is stale — it
describes an early "Chief of Staff model: intent register, domain health,
four-cabinet coordination layer" design that predates today's actual build
(Daily Briefing, Career Focus pipeline, Build Discipline queue, Operations
agent).

Replace the Guild entry with the text below, finalized this session through
Claude.ai/Grok review.

The new Guild entry (76 words) is substantially longer than the stale one
it replaces (~16 words) — to keep "What's running" from growing far past
its neighboring columns, Curator's entry is also tightened (see below).
Curator's actual product (scoring, delivery, schedule) is unchanged — this
is a copy trim only.

**Mein Deutsch is unchanged** — confirmed still accurate against what's
live. Do not touch.

---

## New Guild copy (76 words)

> Guild is where the agents and I work together on design, build, and
> operations, with the intent to learn and improve over time. Ideas being
> discussed are turned into features, then specs, then built capabilities,
> and ongoing operations. Guild is intentionally cross-domain (Curator,
> German, …), with build and operations its particular focus. A Chief of
> Staff role is also part of Guild — it coordinates the agents, but
> importantly, also keeps my goals and private concerns in mind.

Single paragraph — same structure as Curator/Mein Deutsch, no special
handling needed.

---

## Curator copy — tightened (30 words, was 41)

**Current:**
> Curator v1.1 — Geopolitics and finance briefing. ~700 RSS and X candidates
> scored daily by a reasoning model with your learned profile. Top 20 to the
> web portal, top 10 to Telegram at 7 AM. In daily use since February 2026.

**New:**
> Curator v1.1 — Geopolitics and finance briefing. ~700 candidates scored
> daily by a reasoning model against your learned profile, top 10 to
> Telegram at 7 AM. In daily use since February 2026.

Single paragraph, unchanged structure — drops the RSS/X source breakdown and
the web-portal delivery mention, keeps the scale (~700), mechanism (reasoning
model / your learned profile), delivery (Telegram 7 AM), and proof point
(daily use since Feb 2026).

---

## Layout check (after both edits)

Even with Curator trimmed, "What's running" will likely still be somewhat
taller than "What this is" and "About" — Guild's new entry alone is longer
than Curator's old one. If the three cards are equal-height/stretched
(flexbox `align-items: stretch` or similar), growing the middle card means
the other two also grow, leaving empty space at their bottoms — that would
look worse than the middle card simply being a bit taller on its own.

**Ask:** after applying both text changes, check how the three cards behave.
If they're forced to equal height, see whether removing that constraint
(cards size to their own content, uneven bottoms) looks better than the
stretch. Report back either way — this may be a "looks fine, no action
needed" or a small follow-up CSS tweak.

---

## Definition of Done

- [ ] Locate the Guild entry within the "What's running" section of the
      minimoi.ai landing template
- [ ] Replace Guild's content with the text above (single paragraph)
- [ ] Replace Curator's content with the tightened text above (single
      paragraph, same structure)
- [ ] Mein Deutsch entry confirmed unchanged — no edits
- [ ] Layout check per above — report whether cards stretch to equal height,
      and whether removing that constraint (if present) looks better now
      that Guild is longer
- [ ] Visual check on minimoi.ai — both entries render cleanly, no leftover
      stale "intent register / four-cabinet" language anywhere on the page
- [ ] Robert confirms on the live site

---

## Commit

```bash
git add <minimoi.ai landing template path>
git commit -m "content: refresh Guild + tighten Curator on minimoi.ai landing page

Guild: replaces stale 'Chief of Staff model / intent register / four-cabinet'
design language with a description of Guild as where agents and Robert work
on design, build, and operations together — ideas to features to specs to
built capabilities to ongoing operations, intentionally cross-domain
(Curator, German, ...), with a Chief of Staff role coordinating agents and
keeping personal goals/concerns in view.

Curator: trimmed ~700-candidate description to offset Guild's length
increase (product/schedule unchanged, copy only).

Finalized via Claude.ai/Grok review. Mein Deutsch entry unchanged."
git push origin main
```

---

*Spec · Guild · 2026-06-13*
