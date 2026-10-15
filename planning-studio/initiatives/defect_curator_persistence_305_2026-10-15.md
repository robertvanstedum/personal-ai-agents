# Curator container-only state — spec (issue #305)

**Status:** draft for review, 15 Oct 2026. Owner: Claude Code (spec + fix).
Reviewer: Codex (actual diff). Production steps need Robert's approval.
Branch `claude/curator-persistence-305`, cut from `12fc080a`, which is the
commit the production Curator image `minimoi/curator:12fc080` was built from.
Nothing has been run on production by this work.

## 1. What is true now

From Codex's read-only audit (15 Oct) plus a read of the deployed code:

- **Two files changed in production after the container started (14 Oct 18:36Z):**
  `/app/signal_store.jsonl` and `/app/x_pull_state.json`. Both are gitignored, so
  every earlier Curator replacement started them from nothing. Only 12 KB of
  signal events exist now. Earlier event history was probably discarded at
  earlier deploys, but that is **not proven**.
- **Four unmounted paths look unchanged since the image was built:** comments
  (`data/annotations`), the research library, research `agent/config.json` and
  `curator_sources.json`.
  - They are git-tracked (the 5 comment files date from March).
  - Their mtimes equal the image build time (14 Oct 18:28Z).
  - The writable-layer diff showed 0 changes for comments and library.
  - So production's current copies are the repo's copies. Any comment written
    in production before 14 Oct was lost when an earlier container was
    replaced; that is not established either way.
  - `curator_preserve.sh check` confirms this per path.
- **Live risk.** `deploy_scoped_release.sh:137` recreates Curator whenever a
  merge to main classifies Curator as changed. There is no Curator gate. The
  next such merge discards the event log and the X cursor. With the cursor
  gone, the next pull starts with `last_pull_at = None`
  (`x_pull_incremental.py:45-48`).
  - **Until the gate lands, hold any merge that touches Curator.**
- `deploy_scoped_release.sh:213` runs `docker image prune -af`. The old image
  then survives only in ECR, and the old container's layer is gone for good.

## 2. Path map

| State | Writer (deployed code) | How it writes | Target store |
|---|---|---|---|
| General comments | `scripts/annotations.py:15,52` | rewrites a day file | dir bind `/opt/minimoi/data/annotations` → `/app/data/annotations` |
| Signal event log | `domains/curator/signal_store.py:23,59` | append | env `CURATOR_SIGNAL_STORE_PATH=/app/data/curator/signal_store.jsonl` (already mounted) |
| X pull cursor | `scripts/x/paths.py:10`; `x_pull_incremental.py:56-58` | **temp file + rename** (a single-file bind would fail) | env `CURATOR_X_PULL_STATE_PATH=/app/data/curator/x_pull_state.json` (already mounted) |
| Research library | `agent/research.py:966` (README); read by `research_routes.py:370` | rewrite | dir bind `/opt/minimoi/data/research-library` → `…/research-intelligence/library` |
| Research query config | `agent/candidates.py:94`; `research_routes.py:262` | in-place `write_text` | file bind `/opt/minimoi/data/research-config/config.json` → `…/agent/config.json` |
| Source trust list | `curator_intelligence.py:48,131`; `curator_priority_feed.py:189,213` | in-place `write_text` | file bind `/opt/minimoi/data/curator-config/curator_sources.json` → `/app/domains/curator/curator_sources.json` |
| Static topics | `agent/research.py:486-488` | — | **Do not mount.** If the folder exists, sessions go there instead of the durable `data/threads`, so an empty mount would redirect writes. |
| Core data, archive, history, costs, interests, research data | — | — | Already mounted; unchanged. |
| Daily-run outputs: `curator_cache/`, `curator_output.txt`, `curator_index.html`, `curator_latest.html`, `curator_briefing.html` | `curator_rss_v2.py:1475,3352,3481,3522,3531` | rewritten each run | **Rebuilt, not mounted (yet).** A replacement between runs shows the git copy of the briefing, and feedback on that day's articles fails until the next run. Snapshots keep them. Follow-up, not in B. |

Signal events and the X cursor move into the existing `data/curator` mount,
behind an env override that defaults to the old path. That needs no new
single-file binds and keeps dev unchanged.

## 3. Fix, in order

**A. Preserve (this branch, ready for review): `scripts/operations/curator_preserve.sh`**
- `check` is read-only. It lists each known path as mounted, unmounted or
  absent, counts other writable-layer files outside the mounts, and says
  whether a Curator job is running.
- `snapshot`:
  1. Refuses while a Curator job is running.
  2. Stops the container so nothing writes mid-copy.
  3. Copies every known path, plus every other file the writable layer added or
     changed outside the mounts (compiled Python excluded and recorded).
  4. Writes `FILES.sha256`, `ARCHIVES.sha256`, `PATHS`, `docker-diff.txt`, and a
     `SNAPSHOT` record (container ID, image reference and ID, mounts, Compose
     file hash).
  5. Writes everything under a `.partial` name and renames it only when
     complete. Root-only, under `/opt/minimoi/backups/curator-preserve/`. This
     script never prunes. The `backup_local.sh` 14-day prune only removes
     top-level dated folders, so it doesn't reach these.
  6. Restarts the container afterwards (`--keep-stopped` is for the deploy
     gate). Any failure after the stop also restarts the old container.
- Tested locally against a throwaway container: every mode, busy refusal,
  byte-identical copy, restart on failure.
- **Production use (needs approval):** send the script to EC2 by SSM, as the
  audit did. Run `check`, then `snapshot` outside the midnight–06:00 Chicago
  job window. Keep the result path in the private operator record.

**Production result of A (15 Oct, approved by Robert):** `check`, then
`snapshot`. 30 paths and 35 files were kept, root-only on EC2. Curator
restarted in the same container and its health check is OK. Live hashes match
the snapshot; the receipt is in private operator storage. There is no off-host
copy yet (see C).

**B. Gate and mounts (built on this branch; not deployed)**
- `deploy_scoped_release.sh`: if Curator is in the release, run
  `curator_preserve.sh snapshot --keep-stopped` before `up -d`. The EXIT trap
  restarts the old container on any failure, following the Agent A pattern.
- New `curator_seed_state.sh` fills the new host sources from **that
  production snapshot only**:
  - copies when the target is missing;
  - accepts a target with identical content;
  - **refuses** when the content differs or the type is wrong.
  - It never writes an empty directory or file over content, and nothing comes
    from dev or the image.
- Bind-source preflight: missing or wrong-type sources fail before
  recreation. Reuse #179 rather than duplicate it.
- Compose: the binds and env from §2. Code: the two env overrides only.
- Post-deploy check, after the `check_queue_mount.sh` pattern:
  - the mounts are present;
  - host and container SHA-256 match for each moved path;
  - an `ismount` probe passes inside the running container.
- Seeding uses the **deploy-time** snapshot of the container being replaced.
  The 15 Oct snapshot is the baseline and is kept. Seeding from it at deploy
  time would miss events written since, so the seed refuses a snapshot taken
  from any other container.
- The workflow ships the five Curator files to `/opt/minimoi/scripts` as one
  compressed archive inside the existing Compose-sync SSM command.
- The preflight covers all of Curator's file binds, including the
  `curator_costs.json` case in #179; #179 stays open for the other services.
- Tests: `tests/test_curator_persistence_deploy.py` covers map vs Compose,
  ordering, env overrides, and every seed/preflight outcome. A rehearsal with
  throwaway containers ran the real scripts through two replacements: state
  identical after the first, new writes kept after the second, archive
  untouched. It also confirmed that the preflight and the post-deploy check
  each block their broken case. No Curator image, provider call or production
  host was used.
- A busy refusal (a Curator job running) fails the deploy with the old
  container still running. Retry the deploy after the job finishes.

**C. Stop data promotion and fix backup coverage**
- `scripts/sync_data.sh` refuses to run and points to this spec.
  `bootstrap_ec2.sh:45` loses its instruction. #99's Mac-to-production step
  stays superseded.
- Read the **installed** `backup_local.sh` and `backup_s3.sh` first; they differ
  from the repo. Add the new host paths and `backups/curator-preserve` to
  coverage. Verify a restore into an isolated folder.

**C backup result (15 Oct, read-only; receipts in private operator storage):**
- **What is installed.** `backup_local.sh` copies a fixed list of paths, not
  all of `data/`. The repo copy (rsync of all of `data/`, Postgres dump,
  agent_logs) was never installed. `backup_s3.sh` copies that day's dated
  folder plus a Postgres dump. Dropbox gets a weekly copy of the local dated
  folder.
- **Gaps.** None of the #305 paths was backed up: `annotations`,
  `research-library`, `research-config`, `curator-config`. Neither was
  `curator_costs.json`, nor `backups/curator-preserve`, which had no off-host
  copy.
- **Restore check.** The 15 Oct S3 copy of `curator/` and
  `curator_history.json` was restored with the instance role into a
  temporary folder, then removed:
  - 97 files came back, byte-identical to the local dated copy;
  - the JSON parses and the history has its full entry count.
- **This PR.** The repo copies become the installed scripts plus:
  - four `rsync_path` lines for the #305 folders and one for
    `curator_costs.json`;
  - an additive S3 sync of `backups/curator-preserve` (no `--delete`,
    `*.partial` skipped).
  - `tests/test_backup_coverage.py` fails if a host path in
    `curator_state.map` is ever missing from the backup.
- **Install (needs Robert's approval).** Copy both scripts to
  `/opt/minimoi/scripts` over SSM, keeping the old copies beside them. No
  cron change. Then read back the next 02:00 and 03:00 runs.
- **Retention, made explicit:**
  - Local dated folders: pruned after 14 days, the same as now. Each one is
    in S3 the same day.
  - Preservation snapshots: never pruned, on the host or in S3.
  - S3: never deleted by our scripts. The bucket is versioned, but it has an
    **expiry rule covering every object**. With versioning on, that rule
    hides old backups rather than destroying them.
  - **Decision for Robert:** remove that rule, or limit it to an explicitly
    disposable prefix, so all production history stays restorable without
    version recovery. It is an AWS console or root-credential change, not
    part of this PR.
- **Outside Curator, still not backed up (follow-up, not #305):**
  - `data/guild/` (CoS context);
  - `data/model_gateway_receipts.jsonl`;
  - `data/research_dives/`.

**D. Readers (linked, not blocking A–B)**
- Dive generation reads research `data/annotations/research`
  (`generate_dive.py`), but general comments write
  `/app/data/annotations/research/<topic>`. Proposal: dives read both locations,
  with no data moved and attribution kept.
- The Library/bookmark history-path question gets checked against deployed
  code before anything changes.

**D result (15 Oct, deployed code = `86c3b658`):**
- Dives: `generate_dive.py` now reads research `data/annotations/research/<topic>`
  and then `/app/data/annotations/research/<topic>` (the durable mount).
  - Nothing is moved or rewritten, and records are used exactly as written,
    so `domain`, `page` and `ref_*` keep their attribution.
  - Only a record identical in every field, found in both folders, is
    counted once. Records that share a `note_id` but differ are all kept
    (Codex review).
  - Comments are ordered by timestamp.
- Library/bookmarks, **no code change yet**:
  - Every history writer uses `/app/curator_history.json`, the mounted host
    file `data/curator_history.json`:
    - `curator_rss_v2.py:1479` (cron output dir);
    - `curator_feedback.py:165,1616`;
    - `scan_job.py:19`.
  - The readers in `curator_intelligence.py:47` and `curator_server.py:1079`
    (archive sources) use that same root file.
  - Exception: `/api/library` (`curator_server.py:365`) reads
    `CURATOR_DATA_DIR/curator_history.json`. Compose doesn't set
    `CURATOR_DATA_DIR`, so that is `/app/data/curator/curator_history.json`,
    a different file. If it is missing, the Library shows no bookmarks; if it
    is an old copy, the bookmarks it shows are stale.
  - Before choosing a fix, read production read-only:
    - whether `/opt/minimoi/.env` sets `CURATOR_DATA_DIR`;
    - whether `data/curator/curator_history.json` exists, and its size and
      mtime next to the root file.
  - Likely fix: `/api/library` reads the root history file. That fix moves no
    data and deletes nothing.

**E. Daily-run outputs: proposal (15 Oct, for Codex review and Robert's decision)**

Checked against deployed code `86c3b658`. The cron runs
`curator_rss_v2.py` inside the container with cwd `/app`.

| Output | Who reads it | Proposal |
|---|---|---|
| `curator_cache/<hash>.json` (`curator_rss_v2.py:1475,1520`) | `curator_feedback.py:166,225` loads an article by hash, date-rank or `yesterday-N`; Library enrichment, `curator_server.py:333` | **Mount it.** This is not a daily file. It is the growing store of full article records that the mounted `curator_history.json` points to. Each container replacement empties it, so feedback and scans on any earlier article fail with "not in cache" even though the history still lists the article. |
| `curator_output.txt` (cwd, `:3481`) | `curator_feedback.py:34,90` (feedback CLI on today's articles); `core/telegram/telegram_bot.py:183` | **Move behind an env override** into the mounted `data/curator` folder, defaulting to the old path, as was done for the event log. No single-file bind. It is rewritten every run, so only the window between a replacement and the next run is at risk. |
| `curator_latest.html`, `curator_briefing.html` (`domains/curator/`, `:3522,3531`) | `/briefing` and `/curator_briefing.html` serve them only as a fallback when `data/curator/curator_latest.json` (already mounted) can't be loaded | **Don't mount.** Both are git-tracked, and a bind would shadow the tracked copy. The normal path renders from the mounted JSON. |
| `curator_index.html` (cwd `/app`, `:3352`) | `/curator_index.html` serves `domains/curator/curator_index.html`, a different path | **Don't mount.** The writer and the route already point at different files, so nothing a mount keeps is ever served. Leave it as a separate small cleanup. |

**E built (15 Oct; Robert approved building it; it deploys with the other
#305 fixes after review):**
- `curator_cache` is a new map kind, `appdir`: a dir bind for a folder the app
  creates itself (not in the image).
  - If the deploy-time snapshot holds it, the seed copies it.
  - If the snapshot records it absent, the seed creates it empty. The
    container had none, so the empty mount hides nothing.
  - `dir` keeps refusing that case: an absent `annotations` would be an
    anomaly.
- `CURATOR_OUTPUT_PATH=/app/data/curator/curator_output.txt` (a `moved` row).
  The writer (`curator_rss_v2.py`) and every active reader use it:
  `curator_feedback.py`, `flag_article.py`, `core/telegram/telegram_bot.py`
  (run inside the Curator container by the EC2 cron),
  `scripts/send_briefing_telegram.py`. A test fails if a new reader skips it.
  - The bot's old default, `core/telegram/curator_output.txt`, never existed.
    The env fixes it in production; the dev default is unchanged.
- `curator_preserve.sh` knows both old paths, so the first deploy snapshots
  and seeds them. Later deploys find them mounted, or absent at the old path.
- Production read (15 Oct, after #306): the 18:44 replacement had already
  emptied `/app/curator_cache` and removed `curator_output.txt`. They come
  back at the next daily run, so the first deploy of E should come after a
  run.
- Disk: ample headroom on EC2 (receipt in private operator storage); the
  folder grows a few MB a day.

## 4. Rollback

- B's changes are additive host paths. To roll back: the previous Compose file
  and the previous image tag (still in ECR), with the snapshot as the source of
  truth for container-only state.
- Restore = copy from the snapshot's `tree/` back into the mounted host path.
  Never from the image or from dev.

## 5. Decisions for Robert

1. **Config ownership.** Once `config.json` and `curator_sources.json` are bound
   to production copies, repo edits to them no longer reach production on their
   own; a reviewed merge step is needed. **Decided by Robert, 15 Oct: production
   wins for both files.** The source trust list grows in production and is not
   kept in step with dev. Sync runs the other way: **dev is refreshed from
   production**, never production from dev. That sync is a separate follow-up
   and is not in B. Until the research config is split (repo-owned settings
   vs. production-owned saved searches), any repo change to either file needs
   an explicit, reviewed "apply to the server copy" step.
   Today both production copies are byte-identical to the repo's.
2. **Snapshot retention.** Recommended: never prune automatically. Copy off the
   host alongside the S3 backup, after a capacity check.
3. ~~Approval to run `check` then `snapshot`~~ — approved and done 15 Oct.
4. **Deploy approval for B**, after Codex's diff review. It will run outside the
   midnight–06:00 Chicago job window.
