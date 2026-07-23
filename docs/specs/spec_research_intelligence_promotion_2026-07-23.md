# Specification: Promote Research Intelligence from PoC Location to Official Domain

**File:** `docs/specs/spec_research_intelligence_promotion_2026-07-23.md`
**Date:** July 23, 2026
**Status:** Backlog; future work; not approved to build
**Priority:** Normal
**Owner:** Robert
**Implementation agent:** Claude Code after a separately approved build handoff
**Planning/registration:** Register in Guild's build queue; no GitHub issue is
required by this handoff

## Summary

Move Research Intelligence out of `_NewDomains/research-intelligence/` — a
legacy staging location from before this repo's `domains/` convention
existed — into a proper domain location, and correct the documentation that
currently describes it as a superseded PoC. It has been in continuous,
real production use since March; the location and the docs haven't caught
up to that fact.

This is a structural and documentation correction, not a feature build. No
new capability is being added.

## Background

`_NewDomains/` was a staging area for domains not yet promoted to the
`domains/` convention used by German, Portuguese, Guild, and CoS. The July
21–22 repository cleanup (Phase 1) and the subsequent Phase 2 reorganization
(build-queue item #140) both explicitly left `_NewDomains/research-intelligence/`
in place — Phase 2's own dependency map flagged it as live production code
during a hard-stop investigation, and Robert chose partial extraction,
excluding it, specifically because it was still active.

That exclusion was correct at the time and remains correct: this is not
dead code. It surfaced again this week when a GitHub visibility question
("why is `_NewDomains` still on page one") led to a proposal to archive the
whole subtree as a superseded PoC. That proposal was based on
`ROADMAP.md`'s own summary line — "Research Intelligence | PoC pilot (Mar)
· merged into Curator's Deep Dive as its production home (Jun)" — which is
incorrect. It conflates two different things: Curator's own small
`domains/curator/deep_dive.py` (a citation-follow script) and this
subsystem's actual "Deeper Dive" generation
(`_NewDomains/research-intelligence/scripts/generate_dive.py`). Research
Intelligence was never merged into `deep_dive.py`; it has run continuously
and independently the entire time.

## Problem

1. **Location.** The code lives under a directory name (`_NewDomains`) that
   signals "not yet promoted" and sorts to the top of every file listing by
   accident of alphabetization — not because it's important, but because
   an underscore sorts before lowercase letters. A reviewer has no way to
   tell from the path that this is live, actively-used production code
   tightly integrated with Curator.
2. **Documentation is wrong, not just stale.** `ROADMAP.md` states this was
   "merged into Curator's Deep Dive" and is done. It wasn't, and it isn't.
   Anyone reading the roadmap — including an AI agent reasoning about what's
   safe to archive — will reach the same wrong conclusion that was just
   caught before it did real damage.
3. **Real usage, not reflected anywhere as "official."** Topics have
   accumulated real session history over months (Empire Landpower: 14
   sessions; Hellscape Taiwan Porcupine: 10; Gold Geopolitics: 6, and
   others), a daily launchd job (`com.user.research-expiry`) runs against
   it every morning, and it's reachable through Curator's own navigation.
   Nothing about its current status communicates "this is a real feature,"
   which is exactly how it almost got archived as one.

## Goals

1. Move the code, data, and supporting files from
   `_NewDomains/research-intelligence/` into a proper domain location, with
   git history preserved.
2. Update every hard-coded path this move touches: `research_routes.py`'s
   subprocess calls into `agent/research.py` and `agent/run.py`,
   `curator_server.py`'s template/static wiring for the `web/` directory,
   `.dockerignore`'s allowlist, the `com.user.research-expiry` launchd
   plist, and `tests/research_uat.py`'s hard-coded paths.
3. Correct `ROADMAP.md`'s entry to accurately describe this as a live,
   in-production feature — not superseded, not merged into something else.
4. Correct or archive `BACKLOG.md`'s "Research Intelligence Agent" section
   consistent with its real status (several items already marked fixed;
   the section itself should stop reading like an open PoC backlog once
   this promotion lands).
5. Preserve every piece of real data: `data/threads/`, `data/dives/`,
   `data/leanings/`, `data/groups/`, `data/feedback/`, `data/reading_room/`,
   `data/sources/`, `data/observations/`, `data/seen_urls/`, `library/`,
   `topics/`.
6. Fix the one known remaining credential-handling gap in this subsystem
   consistent with PR #122's fix to `research.py`/`run.py`: `agent/observe.py`
   has the same unguarded `keyring.get_password()` pattern and was
   explicitly flagged there as a same-class follow-up, not yet fixed.

## Non-goals

- No new Research Intelligence feature or UI change.
- No change to the actual research/triage/scoring logic, prompts, or model
  selection.
- No renaming of the feature itself (stays "Research Intelligence" /
  "Research Desk" in the UI).
- No change to `domains/curator/deep_dive.py` — confirmed a separate,
  smaller, unrelated feature; not in scope here.
- No opportunistic fix of unrelated defects.
- No change to the daily launchd schedule or its behavior, only its path.

## Open design question for Robert

Where should this land? Two reasonable options, not resolved by this spec:

- **`domains/curator/research/`** — nested inside Curator, since
  `research_routes.py` is already part of Curator's own Flask app, shares
  Curator's auth/tier system, and is reached entirely through Curator's
  navigation. Matches how German nests `domains/german/data/`.
- **`domains/research-intelligence/`** — a peer domain, since it has its
  own large, largely self-contained subtree (`agent/`, `web/`, `data/`,
  `library/`, `topics/`, `docs/`, `scripts/`, `prompts/`) that's
  substantially bigger than a typical nested-data folder, and could
  plausibly stand alone the way Guild or CoS do.

Recommendation: `domains/curator/research/`, since the actual *integration*
point (`research_routes.py`, part of `domains/curator/`) is what makes this
live — it is not a standalone service today, and pretending otherwise with
a peer-domain location would overstate its independence. But this is a
real judgment call, not a technical requirement, and should be Robert's
decision before Phase 1 below begins.

## Preconditions

1. PR #122 (research session credential handling and silent-failure fixes)
   must be merged and verified in dev first — moving broken code makes the
   eventual move harder to verify cleanly.
2. Current `main` commit must be recorded before work begins.
3. Work must occur on an isolated branch or clean worktree, following the
   same discipline as the Phase 2 repository reorganization (#140).

## Phase 1: Read-only dependency map

Before moving anything, produce a complete map matching the rigor of Phase
2's Curator slice work — this subsystem has already demonstrated (via PR
#122's investigation) that a plain filename/import grep misses real
coupling. Specifically confirm:

- Every file the moved code reads or writes via `Path(__file__)`-relative
  computation (the same class of bug PR #122 found and fixed for
  `research.py`/`run.py` applies to `agent/feedback.py`, `agent/threads.py`,
  `agent/observe.py`, `agent/thread_expiry.py`, and
  `scripts/generate_dive.py` — none of these were audited path-by-path the
  way `research.py`/`run.py` were).
- Every `.dockerignore` allowance currently scoped to
  `_NewDomains/research-intelligence/{web,agent,scripts,library,topics,prompts}/`
  and whatever the new location needs instead.
- Every docker-compose volume mount referencing
  `_NewDomains/research-intelligence/data` (both `docker-compose.yml` and
  `docker-compose.prod.yml`) — this is bind-mounted today and the mount
  target must move with the code.
- The `com.user.research-expiry` launchd plist's hard-coded
  `ProgramArguments` and `WorkingDirectory` paths.
- `tests/research_uat.py`'s hard-coded paths (already patched once before
  to match "actual repo layout under `_NewDomains/research-intelligence/`"
  per its own docstring — will need the same correction again).
- Every doc reference: confirmed present in `CHANGELOG.md`, `BACKLOG.md`,
  `ROADMAP.md`, and multiple files under `docs/`. Each needs either a path
  update or, where the reference is to now-historical planning content
  (e.g. `docs/BUILD_ResearchWebUI_2026-03-22.md`), left alone with a note
  of why.

### Required deliverable

A manifest matching Phase 2's dependency-map format, plus a proposed
sequence of vertical slices. Robert approves the target location (see
"Open design question" above) and the manifest before Phase 2 begins.

## Phase 2: Approved vertical migration

Expected shape, subject to what Phase 1 finds:

1. Move `agent/`, `web/`, `library/`, `topics/`, `prompts/`, `data/`,
   `docs/`, `scripts/`, `launchd/` as one coherent slice (this subsystem is
   more internally interdependent than Curator's own files were — a
   partial move is less likely to be safe here than it was across Curator's
   16 files).
2. Update `research_routes.py` and `curator_server.py`'s references.
3. Update `.dockerignore`, both compose files' volume mounts, and the
   launchd plist.
4. Fix `agent/observe.py`'s credential handling in the same slice (Goal 6).
5. Update `ROADMAP.md` and `BACKLOG.md` in the same PR — the whole point of
   this promotion is that the docs stop being wrong the moment the code
   moves, not in a follow-up.

## Required verification

Matching Phase 2's Curator-slice rigor, adapted for this subsystem:

- Full test suite, plus a manual run of `tests/research_uat.py` (not
  automated, but this is the only existing acceptance check for this
  subsystem's actual triage/scoring behavior — worth running by hand).
- Docker image builds clean; every moved file's imports and subprocess
  paths verified working *inside* the built image, not just in a local
  venv.
- A real end-to-end research session run to completion in dev (same
  verification PR #122 already did for the pre-move location) — confirms
  nothing broke in the move.
- `com.user.research-expiry`'s new path confirmed runnable manually before
  relying on the next scheduled 6am run.
- Curator's own health checks (briefing, save, scan, deep dive) — this
  subsystem shares Curator's Flask app; a bad import here can take down
  Curator entirely, the same risk class Phase 2 flagged for shared
  utilities.
- Production smoke test and a recorded rollback point before the final
  merge, consistent with every other Phase 2 slice.

## Acceptance criteria

1. No content remains under `_NewDomains/research-intelligence/`.
2. `ROADMAP.md` and `BACKLOG.md` accurately describe this as a live,
   in-production feature.
3. Every real data path (`data/threads/`, `topics/`, `library/`, etc.)
   verified intact post-move — before/after checksum comparison, same
   discipline as Phase 2 Slice 5's runtime-state move.
4. The daily expiry job, the manually-triggered web sessions, and Curator's
   own health all verified working post-move, in dev, before any
   production promotion.
5. `agent/observe.py`'s credential handling matches the fix already applied
   to `research.py`/`run.py`.

## Build-queue registration

Register this as:

- **Title:** Promote Research Intelligence from PoC location to official domain
- **Status:** `backlog`
- **Priority:** `normal`
- **GitHub issue:** `null`
- **Blocked reason:** Future work. Precondition: PR #122 merged and verified
  first. Robert must choose the target location (`domains/curator/research/`
  vs. a peer `domains/research-intelligence/`) before Phase 1 begins.
- **Notes:** Corrects a real documentation error (`ROADMAP.md` incorrectly
  states this was merged into Curator's Deep Dive and is superseded — it
  wasn't and isn't) that nearly caused live production code to be archived
  as dead. Not a feature build — a location and documentation correction
  for a subsystem that has been in continuous production use since March.
