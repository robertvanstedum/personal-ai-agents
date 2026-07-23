# Spec: Repository Root Consolidation

**File:** `docs/specs/spec_repository_root_consolidation_2026-07-22.md`
**Date:** July 22, 2026
**Status:** Done — complete, deployed, verified
**Priority:** Normal
**Owner:** Robert
**Implementation agent:** Claude Code

## Summary

Reduced the public repository's root folder count from 26 to 17, and
extracted personal/private material into `mini-moi-private`. Executed in
three separately-reviewed, separately-merged PRs, each verified in
production before the next began.

## What was done

**Phase 1A — private material extraction ([PR #107](https://github.com/robertvanstedum/personal-ai-agents/pull/107), merged `7d65fc1`):**
- Transferred 77 files to `mini-moi-private` (branch
  `review-later/2026-07-22-transfer`, commit `871644b`), checksum-verified
  byte-identical before removal from the public repo:
  `job-search/` (21 files), `personal-finance/` (3 files), and 53 files from
  `_NewDomains/` (`career/`, `guild/`, `phase-zero/`, and 12 loose root docs).
- `_NewDomains/research-intelligence/` (159 files) was **not** moved —
  confirmed live production dependency (imported by `curator_server.py`,
  `research_routes.py`, `curator_rss_v2.py`, `curator_feedback.py`,
  `domains/guild/db/{migrate,reconcile}.py`, plus real Docker volume mounts
  in both compose files). Stays in the public repo.
- `.env.ec2` untracked from git (`git rm --cached`); file preserved on disk,
  never displayed or copied. Credential categories present (by name only,
  not value): `POSTGRES_PASSWORD`, `DATABASE_URL`. Rotation not performed —
  flagged as a separate security follow-up, not part of this spec.

**Phase 1B — low-risk public consolidation ([PR #108](https://github.com/robertvanstedum/personal-ai-agents/pull/108), merged `7563c19`):**
- `archive/` → `docs/archive/legacy-repository/`
- `guild/` → `docs/archive/founding/guild/` (distinct from the active
  `domains/guild/` application)
- `journal/` → `docs/journal/`
- `memory/project_drill_test_suite.md` → `tests/fixtures/`
- `reports/` → `data/reports/`
- Deleted (git history sufficient, not archived): `_active_drill_state.json`,
  `_drill_list_state.json` (stale root duplicates), `curator_library.html`,
  `curator_preview.html` (unreferenced by any live route)

**Phase 1C — tooling and infrastructure ([PR #109](https://github.com/robertvanstedum/personal-ai-agents/pull/109), merged `6041c69`):**
- `tools/` → `scripts/tools/`
- `launchd/` → `infrastructure/launchd/`
- `nginx/` → `infrastructure/nginx/`
- `docker/` deliberately not moved in this phase

## Result

Root folders: **26 → 17**. Root files: 60 canonical documents, config, and
compose files, unchanged in count (Phase 1 was folder-focused).

All three PRs deployed through the full CI/CD pipeline and verified against
production (`minimoi.ai` returning 200) after each merge. Full test suite
(88 passed, 2 skipped) and `docker compose build` confirmed clean at every
stage, including the final fully-merged state.

## Explicitly not part of this work

- **Repository Application Reorganization Phase 2** (build queue #140,
  `docs/specs/spec_repository_application_reorganization_phase2_2026-07-21.md`)
  — the remaining production Python/HTML application cluster at root
  (`curator_server.py` and companions). Stays `backlog`, unaffected by this
  spec's completion. Not started, not implied by this closure.
- Credential rotation for `.env.ec2`'s contents — flagged, not performed.
  Separate security follow-up.
- The pre-existing malformed `infrastructure/launchd/com.vanstedum.telegram-webhook.plist`
  (invalid XML, confirmed present before this spec's changes) — separate
  operational follow-up.
- Local launchd job state on Robert's Mac (an unrelated native
  `com.vanstedum.minimoi-portal` job left unloaded from an earlier session
  task) — separate operational follow-up, not a reorganization defect.

This spec is complete. No further public root-folder consolidation is
planned or implied.
