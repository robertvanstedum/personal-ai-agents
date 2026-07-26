# Specification: Repository Application Reorganization Phase 2

**File:** `docs/specs/spec_repository_application_reorganization_phase2_2026-07-21.md`
**Date:** July 21, 2026
**Status:** In build. Phase 2A dependency map delivered 2026-07-26; Codex
review of that manifest pending before Phase 2B slices begin.
**Priority:** Normal
**Owner:** Robert
**Implementation agent:** Claude Code after a separately approved build handoff
**Planning/registration:** Register in Guild's build queue; no GitHub issue is
required by this handoff

## Summary

Reorganize the remaining production application files at the repository root
into coherent domain and operational locations. The work begins with a
read-only dependency map and proceeds, if separately approved, through small
vertical migrations with production verification after each merge.

This is a structural cleanup, not a feature build. Its purpose is to make the
repository easier for a reviewer and maintainer to understand without breaking
the paths on which the running system depends.

## Background

The July 21 repository cleanup established a clearer public landing surface and
moved a first low-risk group of standalone scripts and tests. PR #101 handled
the documentation and historical-file cleanup. PR #102 relocated the safe
script/test slice and verified those paths inside the Docker image.

The remaining root cluster contains production application code and entry
points. These files are more tightly coupled to imports, Flask and nginx
routes, Docker commands and volumes, cron and launchd jobs, templates, static
assets, runtime-created files, private synchronization, and absolute paths on
the production host. Moving them for appearance alone would create unnecessary
production risk.

## Problem

The repository root still mixes canonical project documents and configuration
with production application files. A reviewer cannot immediately distinguish:

- intentional root entry points;
- Curator domain implementation;
- genuinely shared platform code;
- operational/runtime helpers;
- compatibility paths; and
- inactive or historical code.

The structure should communicate those roles clearly, but runtime behavior and
proven dependencies must determine the target layout.

## Goals

1. Produce a complete, reviewed dependency map before moving any remaining
   production application file.
2. Consolidate Curator implementation under `domains/curator/` where the
   dependency map shows that domain-relative execution is safe.
3. Leave only canonical documents, essential repository configuration, and
   intentionally retained entry points at the root.
4. Preserve application behavior, data paths, deployment behavior, schedules,
   and private-repository synchronization.
5. Preserve history and make rollback straightforward.
6. Complete the work in independently reviewable vertical slices rather than a
   single repository-wide move.

## Non-goals

- No new application feature or UI change.
- No generic `src/` migration for cosmetic consistency.
- No new shared abstraction unless existing runtime usage proves it is shared.
- No deletion of substantive history.
- No movement of private material into the public repository or a public
  archive.
- No Cloudflare DNS, tunnel, Workers, or Pages change.
- No model, prompt, scoring, search-quality, or cost-policy change.
- No opportunistic fix of unrelated defects.
- No change to the journal or broader governance workflow.

## Preconditions

Before the dependency-map work begins:

1. PRs #101 and #102 must be merged.
2. Their AWS deployments and production verification must be green.
3. The current `main` commit must be recorded.
4. The existing dirty and untracked local files must be listed and protected.
5. Work must occur on an isolated branch or clean worktree.

Meeting these preconditions authorizes only the read-only mapping phase. It
does not authorize file moves.

## Phase 2A: Read-only dependency map

Inventory every remaining root Python, shell, JSON, and HTML file. For each
file, record:

- purpose and current runtime status;
- imports and importers;
- direct callers and command-line entry points;
- Flask and nginx routes;
- Docker `COPY`, command, volume, working-directory, and health-check references;
- cron, launchd, and other scheduler references;
- GitHub Actions references;
- template and static-file lookups;
- tests and fixtures;
- runtime-created paths and persisted data;
- private-repository synchronization paths;
- production absolute paths under `/opt/minimoi`;
- documentation and operational-command references;
- proposed destination;
- whether a temporary compatibility launcher is required; and
- verification and rollback requirements.

Classify each file as one of:

- intentional root entry point;
- Curator domain application;
- proven shared platform component;
- operational command;
- development utility;
- compatibility path;
- live archive/runtime dependency; or
- historical candidate requiring a separate decision.

### Required deliverable

Return the complete manifest and a proposed series of vertical moves. Report
ambiguous files instead of guessing. Robert must approve the dependency map and
each proposed slice before implementation begins.

### Phase 2A deliverable — completed 2026-07-26

Full manifest below, verified against actual code (file:line citations, not
proposal wording) — not the same as taking a proposal on faith. Source
investigation: Codex's initial proposal (2026-07-26) plus independent
verification by Claude Code, including one dedicated caller-mapping pass.
Full working notes: `_working/PLAN_repo_root_cleanup_2026-07-26.md`.

| File/dir | Classification | Callers found | Proposed destination | Compatibility launcher needed? |
|---|---|---|---|---|
| `restart_portal.sh` | Operational command | None found in repo | `scripts/ops/` | No |
| `run_intelligence_cron.sh` | Operational command | None found in repo (doc mentions only) | `scripts/ops/` | No |
| `run_lesen_refresh.sh` | Operational command | None found in repo | `scripts/ops/` | No |
| `run_priority_feed_cron.sh` | Operational command | None found in repo | `scripts/ops/` | No |
| `track_usage_wrapper.sh` | Operational command | Runs via macOS `cron`, not a repo-committed plist (`OPERATIONS.md:335`) | `scripts/ops/` | No |
| `run_curator_cron.sh` | Operational command | `core/telegram/telegram_bot.py:374,484,2360` (**already broken today**, see below); invokes `x_pull_incremental.py` via `cd "$PROJECT_DIR"`; mirrored by `scripts/run_curator_cron_ec2.sh` | `scripts/ops/` | Move together with `x_pull_incremental.py`/`x_to_article.py` in the same slice |
| `start_telegram_webhook.sh` | Compatibility path (already a 2-line shim to `scripts/start_telegram_webhook.sh`) | `infrastructure/launchd/com.vanstedum.telegram-webhook.plist:24` — **absolute path** | Update the plist; shim itself may then be removable | Plist must be updated in the same change, not after |
| `credential_manager.py` | Development utility | Loads `.env` via `Path(__file__).parent / ".env"` (line 55) — self-referential, no external caller by path | `scripts/credentials/` | No, but verify `.env` still resolves correctly post-move |
| `x_auth.py` | Curator domain application (feeds Curator's X ingestion) | None found — uses `keyring`/`tweepy` only, no path math | `scripts/x/` | No |
| `x_pull_incremental.py` | Curator domain application | `x_pull_incremental.py:35-36` computes `SIGNALS_FILE`/`STATE_FILE` via `Path(__file__).parent`; invoked by `run_curator_cron.sh` and `scripts/run_curator_cron_ec2.sh` | `scripts/x/` | Move with `run_curator_cron.sh` in the same slice |
| `x_to_article.py` | Curator domain application | `x_to_article.py:42-43`, same `Path(__file__).parent`-relative pattern | `scripts/x/` | Move with the above |
| `language_coming.html` | Curator domain application | `domains/curator/curator_server.py:1378` — `REPO_ROOT` already computed independent of location; only the filename literal needs updating | Curator templates/static area | No |
| `static/` | **Historical candidate requiring a separate decision** | None found anywhere — grepped every distinctive filename inside it (`portal.css`, background images, `static/public/index.html`, etc.), zero hits. Portal's own `static_folder` points at `minimoi_portal/static`, a different directory entirely. | Not proposed — needs Robert's explicit decision (archive vs. delete vs. confirm live use I missed) before Phase 2B touches it | N/A pending decision |
| `config/` | Proven shared platform component (thin) | Referenced only in docstrings/comments (`domains/cos/backends/grok_backend.py`, `openclaw_backend.py`) and doc cross-links — never loaded by runtime code | `docs/` or `docs/design/` | No — comment-text updates only |
| `curator_archive/` (4 stale Feb 2026 `.html` files) | Live archive/runtime dependency — **but not actually live**: already excluded via `.dockerignore`, never reaches the built image. Production's real archive is a separate volume mount (`/opt/minimoi/data/curator_archive`), unrelated to this git-tracked directory. | None | Archive location alongside other historical docs | No |
| `curator_url_cache.json` | Live archive/runtime dependency — **data-safety flag** | `scripts/enrich_signals.py:53` — `PROJECT_DIR / 'curator_url_cache.json'`, `PROJECT_DIR = Path(__file__).parent.parent` (repo root, independent of the file's own location) | `data/curator/` | Not a launcher issue — a **persistence** issue: this file is not volume-mounted in either `docker-compose.yml` or `docker-compose.prod.yml` today. If genuinely written to at runtime in production, every deploy already wipes it (same class of bug as the old issue #93). Must resolve the mount question as part of this move, not just relocate the file. The code comment citing "decision #5" does not correspond to anything in current `DECISIONS.md` — stale or never written down. |
| Docs (`ARCHITECTURE.md`, `BACKLOG.md`, `CREDENTIALS_SETUP.md`, `DECISIONS.md`, `OPERATIONS.md`, `PROJECT_STATE.md`, `ROADMAP.md`, `VISION.md`, `WAYS_OF_WORKING.md`) | Canonical documents (in scope per this spec's Goal 3, "leave only... at the root") | Referenced by README and archived docs via root-relative links (not individually re-verified per file; expect broad link repair) | `docs/` | No — link repair required across README and archived docs |

**Known adjacent issue confirmed, not folded into this spec's scope:**
`core/telegram/telegram_bot.py` (lines 374, 484, 2360) computes
`BASE_DIR / 'run_curator_cron.sh'` where `BASE_DIR = Path(__file__).parent`
resolves to `core/telegram/` — this reference is **already broken today**,
a leftover from the Phase 2 Slice 2 telegram move (commit `5a3f2ac`),
independent of this cleanup. Moving `run_curator_cron.sh` does not make it
worse. Per this spec's own "Known adjacent issue" precedent (the
`x_adapter.py` case above), this gets its own issue, not silent scope
absorption here.

**Proposed slice order** (supersedes the generic Phase 2B sequence below
with the concrete order for this manifest):

1. Docs move + link repair (`ARCHITECTURE.md` through `WAYS_OF_WORKING.md`) + `config/` (comment-only updates) + `curator_archive/` (dead weight, no callers).
2. `static/` — pending Robert's explicit decision, not auto-included in slice 1.
3. The five no-dependency scripts + `x_auth.py` (zero caller updates).
4. `run_curator_cron.sh` + `x_pull_incremental.py` + `x_to_article.py` together, updating `scripts/run_curator_cron_ec2.sh` in the same PR.
5. `start_telegram_webhook.sh` + the launchd plist absolute-path update, together.
6. `credential_manager.py`, with `.env` resolution re-verified post-move.
7. `language_coming.html`, with the one literal in `curator_server.py` updated.
8. `curator_url_cache.json` — gated on resolving the missing-volume-mount question first; full backup/dry-run/rollback per this spec's existing Phase 2B item 4 and the original Codex proposal's caution.

File the separate `telegram_bot.py` → `run_curator_cron.sh` broken-reference
issue at any point; it does not block or get blocked by any slice above.

## Phase 2B: Approved vertical migrations

The dependency map determines the final order. The expected sequence is:

1. Account for the standalone utilities, scripts, and tests already moved by
   PR #102; do not repeat that work.
2. Identify the smallest coherent Curator runtime slice that can move and be
   verified independently.
3. Consolidate Curator application code under `domains/curator/` through
   reviewed moves that preserve Git history.
4. Move Curator templates, static assets, and configuration only when the
   application supports domain-relative lookup and all callers have been
   identified.
5. Retain thin compatibility launchers at root for one release when existing
   commands or external paths need a transition period.
6. Remove compatibility launchers only in a later, separately reviewed change
   after production usage proves the new paths.
7. Reassess `archive/`, `curator_archive/`, and other ambiguous runtime folders
   only after the new application paths are proven.

Each vertical slice receives its own branch, pull request, test report, explicit
merge approval, deployment, and production verification. Do not run multiple
production-path migrations in parallel.

## Required verification for every implemented slice

- Full automated test suite.
- Local Ollama regression behavior, with the intentional hosted-runner skip
  documented.
- Docker image build.
- Container startup and health checks.
- Verification of every changed import and executable path inside the built
  image.
- Curator Daily, Scan, Dive, Archive, Research, and related route smoke tests.
- German, Portuguese, Guild, and Chief of Staff health checks.
- Cron and launchd path verification.
- nginx and Flask route verification.
- Private-sync dry run.
- Persistent-volume and runtime-created-path verification.
- Public README/link rendering when paths exposed in documentation change.
- Full AWS deployment monitoring after an explicitly approved merge.
- Production smoke tests and a recorded rollback point.

## Known adjacent issue

PR #102 confirmed that `x_adapter.py` could not run inside the Curator container
before that cleanup. Treat this as a pre-existing defect, not as hidden scope
inside Phase 2. It should receive its own issue/spec unless an approved Phase 2
slice directly depends on it; in that case, stop and obtain an explicit scope
decision before combining the work.

## Risks and controls

| Risk | Control |
|---|---|
| A moved file breaks an import or direct command | Complete caller map; test inside the built image; retain compatibility launcher if needed |
| Docker or production still expects a root path | Map all Docker and `/opt/minimoi` references before approval |
| A scheduler silently stops running | Verify cron and launchd definitions before and after deployment |
| Templates, assets, or data resolve relative to the old directory | Record every lookup and test domain-relative behavior before moving them |
| Private data is exposed during cleanup | Privacy-classify destinations; never move private material to public archives |
| A large change obscures the source of a regression | One coherent slice per PR and production verification before the next slice |
| Historical code is mistaken for dead code | Classify by live references and runtime evidence, not filename or age |

## Acceptance criteria

The initiative is complete when:

1. The approved dependency manifest covers every remaining root application
   file.
2. Root application files are organized into coherent, named domain or
   operational locations, except for documented intentional entry points.
3. Curator's runtime structure is understandable from the repository layout.
4. All imports, commands, routes, Docker paths, schedules, templates, assets,
   persistence paths, and private-sync paths point to the approved locations.
5. Every migration slice passed its complete verification set and production
   smoke tests.
6. Temporary compatibility launchers have an explicit removal decision or
   tracked follow-up.
7. No private material or substantive history was lost or newly exposed.
8. The final root presents only canonical documents, essential configuration,
   and deliberately retained entry points.

## Build-queue registration

Register this as:

- **Title:** Repository Application Reorganization Phase 2
- **Status:** `in_build`
- **Priority:** `normal`
- **GitHub issue:** `null`
- **Blocked reason:** None currently. Phase 2A dependency map delivered
  2026-07-26 (see manifest above). Handed to Codex for independent review
  before Phase 2B slices begin implementation.
- **Notes:** PR #102 completed the preliminary standalone script/test move.
  Phase 2A manifest complete as of 2026-07-26 — see table above and
  `_working/PLAN_repo_root_cleanup_2026-07-26.md` for full working notes.
  One slice and one production verification at a time, per the proposed
  slice order above. Do not combine the pre-existing `x_adapter.py` defect
  or the newly-found `telegram_bot.py` → `run_curator_cron.sh` broken
  reference without explicit approval — both get their own issue.
