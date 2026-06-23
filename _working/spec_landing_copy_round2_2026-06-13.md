# Spec — Landing Copy Round 2: minimoi.ai "What's Running"
*mini-moi · Guild*
*Created: 2026-06-13 — Claude.ai*
*For: Claude Code*

---

## What's changing

Follow-on to `spec_guild_landing_copy_2026-06-13.md` (shipped). Three small
text edits, plus a bug fix on Guild.

### Bug: Guild prefix duplication

The live Guild entry currently reads **"Guild — Guild is where the
agents..."** — duplicated. The template prepends "**Guild** —" the same way
it does "**Curator v1.1** —" / "**Mein Deutsch v1.0** —" (this prefix
pre-dates today's edit — the old stale text was "Guild — Professional
operating system..."). The new Guild body text also started with "Guild is
where...", causing the collision.

**Fix:** Guild's prefix becomes "**Guild v0.9** —" (see below — this also
satisfies the "add v0.9, it's at beta" request), and the body text drops
"Guild is" and starts directly with the description ("A place where..."),
using the dash the same way Curator/Mein Deutsch's entries do (the em-dash
carries the meaning of "is"). If the template has a separate name/version
field (like it presumably does for Curator's "v1.1" and Mein Deutsch's
"v1.0"), set it to "Guild v0.9". If Guild's entry is just one text blob with
no separate name field, write the full line as shown below, prefix included.

---

## Curator copy (31 words, was 31)

**Current (live):**
> Curator v1.1 — Geopolitics and finance briefing. ~700 candidates scored
> daily by a reasoning model against your learned profile, top 10 to
> Telegram at 7 AM. In daily use since February 2026.

**New:**
> Curator v1.1 — Geopolitics and finance briefing. ~700 candidates scored
> daily by a reasoning model against your learned profile — counterpoints
> by design, not an echo chamber. In daily use since February 2026.

Same word count as live — drops the delivery-schedule detail ("top 10 to
Telegram at 7 AM"), adds "counterpoints by design, not an echo chamber".
Per Grok review: this reflects the actual intent more directly than the
earlier "intentional friction, not passive consumption" draft — Curator is
built to surface counterpoints and avoid self-reinforcing loops, not just
to require engagement.

---

## Mein Deutsch copy (31 words, was 21)

**Current:**
> Mein Deutsch v1.0 — German language coaching pipeline. Vienna-tested.
> Lesen, Gespräche, Schreiben, Wörter. Anki cards earned from real friction,
> not passive review.

**New:**
> Mein Deutsch v1.0 — German language coaching pipeline. Create personas and
> scenes, hold a real-time conversation, then review the transcript and
> iterate. Vienna-tested. Lesen, Gespräche, Schreiben, Wörter. Anki cards
> earned, not passive review.

Adds the actual workflow loop (create → converse → review transcript →
iterate) before the existing Vienna-tested / skills / Anki sentences, which
stay as-is.

---

## Guild copy (51 words, was 46 incl. duplicate "Guild")

**Current (live, buggy):**
> Guild — Guild is where the agents and I work together on design, build,
> and operations, with the intent to learn and improve over time. Guild is
> intentionally cross-domain (Curator, German, …). A Chief of Staff role
> coordinates agents and keeps my goals and private concerns in mind.

**New:**
> Guild v0.9 — Where the agents and I work together to get better at our
> craft. From ideas through specs, build, and operations — the whole time.
> Guild is intentionally cross-domain (Curator, German, and beyond). A Chief
> of Staff role coordinates the agents while keeping my real goals and
> private concerns in view.

Fixes the duplication using the same convention as Curator/Mein Deutsch —
the em-dash after "Guild v0.9" carries the meaning of "is", so the body
starts directly with "Where..." rather than repeating "Guild is". Per Grok
review: "Where..." (vs "A place where...") and "get better at" (vs
"improve") are more direct/casual, matching the voice established
elsewhere; "craft" is retained per Robert's preference (kept "get better
at" + "craft" together rather than Grok's "what we do"). "and beyond"
replaces the ellipsis — same extensibility meaning, clearer without relying
on punctuation. Chief of Staff line restructured: "the agents... while
keeping my real goals... in view" — "in view" echoes the "visible
pipelines" language used elsewhere in Guild's description. Adds the v0.9
version label, matching Curator v1.1 / Mein Deutsch v1.0's pattern — v0.9
signals Guild is the newest/least mature of the three, currently at beta.

---

## Net effect on "What's running" length

Curator 0, Mein Deutsch +10, Guild +5 → net +15 words (~98 → ~113). Modest
increase — no new layout action expected beyond the `align-items: start`
already applied.

---

## Definition of Done

- [ ] Curator entry updated to new text above
- [ ] Mein Deutsch entry updated to new text above
- [ ] Guild entry fixed — no duplicated "Guild", version label "v0.9"
      present, matches Curator/Mein Deutsch's "Name vX.X —" pattern
- [ ] Visual check on minimoi.ai — all three entries render cleanly, correct
      text, no leftover duplication anywhere
- [ ] Robert confirms on the live site

---

## Commit

```bash
git add <minimoi.ai landing template path>
git commit -m "content: round-2 landing copy edits + fix Guild prefix duplication

Curator: drop delivery-schedule detail, add 'counterpoints by design, not
an echo chamber' — reflects the anti-echo-chamber intent behind the
scoring/profile mechanism (per Claude.ai/Grok review).

Mein Deutsch: add workflow loop description (create personas/scenes -> 
real-time conversation -> review transcript -> iterate) ahead of existing
Vienna-tested/skills/Anki content.

Guild: fix 'Guild - Guild is where' duplication introduced by prior commit
(template prepends 'Guild -', body text also started with 'Guild is
where'). Body now starts 'Where the agents and I...'; prefix becomes
'Guild v0.9 -' per request to mark Guild as beta, matching Curator v1.1 /
Mein Deutsch v1.0's versioned-name pattern."
git push origin main
```

---

*Spec · Landing Copy Round 2 · 2026-06-13*
