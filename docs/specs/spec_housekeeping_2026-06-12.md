# Spec — Housekeeping: Repo Sync Audit + Roadmap File Location
*mini-moi · Guild*
*Created: 2026-06-12 13:24 CDT — Claude.ai*
*For: Claude Code*

Two small, unrelated items bundled together. **When this spec is picked up,
do Part 1 first** — it's a privacy check (career-search data potentially in
the public repo during active job search), Part 2 is pure tidy-up.

---

## Part 1 — Repo sync audit

**Context:** today's 222-file commit ("German sessions, Anki CSVs, lessons,
curator state, guild memory, screenshots, working docs") was pushed to both
`origin` (`personal-ai-agents`, public) and `private` (`mini-moi-private`).
The two-repo strategy intends this category of data — especially "guild
memory" (likely includes `cos_context.json` / career-focus data: target
companies, application statuses, search strategy) — to be private-only. The
spec that was supposed to define the path-based split (`sync_private_repo.sh`
+ `devagent_config.json` private paths) was never completed, so this broad
commit may not have applied any filtering.

**Task — investigation and reporting only, no remediation in this spec:**

1. From today's 222-file commit, list every path that is present on
   `origin/main` (e.g. `git show --stat <hash>`, filtered to what `origin`
   actually has — not just what was in the commit locally).
2. Categorize each path:
   - Looks fine for public (code, docs, templates, config without secrets)
   - Needs review (matches: German session transcripts, Anki CSVs, lessons,
     curator state, guild memory / `cos_context.json`-like files, screenshots,
     `_working/` docs referencing personal/career details)
3. Present both lists to Robert. **Stop here.** If anything in "needs
   review" should not be public, removing it properly requires a git
   history rewrite + force-push (a follow-up commit alone won't remove it
   from history or from anything GitHub may have already indexed) — that's
   a separate spec, written only after Robert reviews this list and
   confirms what needs to go.

---

## Part 2 — Roadmap file location

**Context:** the Roadmap tab (`/guild/build/roadmap`) currently reads
`_working/ROADMAP_2026-06-12.md`. Two problems: `_working/` is for
spec-track + transient files per `docs/HANDOFF_PROCESS.md` — a permanent
living doc doesn't fit that lifecycle (not spec-track, so not protected from
a future archive sweep either). And the dated filename implies a snapshot,
misleading for something meant to be continuously appended to.

**Task:**

1. `mv _working/ROADMAP_2026-06-12.md docs/ROADMAP.md`
2. Update the `/guild/build/roadmap` route to read `docs/ROADMAP.md`
3. Confirm the Roadmap tab still renders correctly after the move
4. If `docs/GUILD.md` has a "Roadmap" section, point it at `docs/ROADMAP.md`
   rather than duplicating content (per the original roadmap doc's closing
   note)

---

## Definition of Done

- [ ] Part 1: categorized list (fine / needs-review) of `origin`-pushed
      paths from today's 222-file commit, presented to Robert
- [ ] Part 1: no remediation action taken without Robert's explicit review —
      if "needs review" is non-empty, that becomes its own follow-up spec
- [ ] Part 2: `docs/ROADMAP.md` exists, `_working/ROADMAP_2026-06-12.md` no
      longer present
- [ ] Part 2: `/guild/build/roadmap` reads `docs/ROADMAP.md`, renders
      correctly
- [ ] Part 2: `docs/GUILD.md` Roadmap section (if present) points to
      `docs/ROADMAP.md`

---

## Commit

Part 1 produces a report, not a commit — no git action unless Robert
approves remediation as a follow-up.

```bash
git add docs/ROADMAP.md docs/GUILD.md domains/guild/
git rm _working/ROADMAP_2026-06-12.md
git commit -m "docs: move roadmap to docs/, point GUILD.md and roadmap
route at it (Part 2 of housekeeping spec)"
git push origin main
```

---

*Spec · Guild · 2026-06-12*
