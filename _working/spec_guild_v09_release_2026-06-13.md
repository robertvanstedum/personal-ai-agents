# Spec — Guild v0.9 (beta): GitHub Release
*mini-moi · Guild*
*Created: 2026-06-13 — Claude.ai*
*For: Claude Code*

---

## What's changing

minimoi.ai's landing page now shows "Guild v0.9" alongside Curator v1.1 and
Mein Deutsch v1.0 (see `spec_landing_copy_round2_2026-06-13.md`). This spec
marks that milestone on GitHub — a release/tag with notes summarizing what
v0.9 actually includes, for anyone (recruiters, collaborators, future-me)
landing on the repo from the "View source on GitHub" link.

**First, check existing conventions** — does the repo already have a
CHANGELOG.md, prior release tags (`git tag -l`), or a version marker
somewhere (package.json, VERSION file, GUILD_BUILD_LOG.md)? Match whatever
pattern already exists. If there's no existing pattern, a GitHub Release
created from a new tag is the simplest path — use the content below as the
release description, and skip any CHANGELOG.md creation.

---

## Release title

> Guild v0.9 (beta)

## Suggested tag

> `guild-v0.9`

(Adjust if an existing tagging convention says otherwise — e.g. if Curator
v1.1 / Mein Deutsch v1.0 already have tags following a different pattern,
match that instead.)

---

## Release description / blurb

> ## Guild v0.9 (beta)
>
> Guild is where the agents and I work together on design, build, and
> operations — and where Curator and Mein Deutsch get pulled into view
> alongside my own goals.
>
> **v0.9 brings:**
> - **Daily Briefing** (`/guild`) — systems health, Career Focus countdown,
>   Build status, and what's ahead, in one place
> - **Build Discipline** — specs move spec → queue → done at `/guild/build`,
>   with automatic classification and archiving (this release went through
>   the same pipeline)
> - **Career Focus** — active opportunity pipeline with LinkedIn-based
>   warm-lead scoring
> - **Chief of Staff + Operations agents** — cross-domain coordination,
>   system health monitoring, staleness detection
> - **Four background intelligence loops** — career focus scout, German
>   domain watch, Curator topic scout, novelty watch
> - **Two-row navigation + typography hierarchy** across Curator, Mein
>   Deutsch, and Guild
>
> **Why beta:** the four loops are newly live (first-fire review this week),
> and Build Discipline — while self-validating — is still young. v0.9
> reflects real daily use, not a finished system.

---

## Commit (if a docs update accompanies the tag — optional)

If GUILD_BUILD_LOG.md or similar already tracks milestones, add a short
entry there rather than (or in addition to) the release. Suggested commit
message either way:

```bash
git commit -m "docs: mark Guild v0.9 (beta) milestone

Guild reaches v0.9 (beta) - Daily Briefing, Build Discipline (spec -> queue
-> done), Career Focus pipeline, Chief of Staff + Operations agents, and
four background intelligence loops, all in daily use. Matches the v0.9
label now shown on minimoi.ai's landing page alongside Curator v1.1 and
Mein Deutsch v1.0."
```

---

## Definition of Done

- [ ] Checked for existing CHANGELOG/tag/version conventions in the repo
- [ ] Tag created (`guild-v0.9` or matching existing convention)
- [ ] GitHub Release created with the description above (or adapted to fit
      existing CHANGELOG format if one exists)
- [ ] Optional docs update committed if GUILD_BUILD_LOG.md tracks milestones
- [ ] Robert confirms on GitHub

---

*Spec · Guild v0.9 Release · 2026-06-13*
