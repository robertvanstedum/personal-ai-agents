# Backup gaps: Guild state, gateway receipts, research dives — spec

**Status:** draft for review, 15 Oct 2026. Owner: Claude Code. Reviewer: Codex
(the diff). Follow-up to #305 and PR #310; this branch is stacked on #310's
`scripts/backup_local.sh`, which is the installed EC2 copy plus the Curator
paths. Nothing has been run on production by this work.

## 1. What is true now

Robert's rule: all production content and history is kept.

A read-only listing of production `data/` on 15 Oct showed three paths that
no backup tier copies (not the dated local copy, so not S3 or Dropbox
either, since both sync from it):

| Path under `/opt/minimoi/data` | What it is | Who mounts it |
|---|---|---|
| `guild/` | Live Build Queue, its lock and journal, CoS context (`cos_context.json`), limits, MC evidence | portal (`/app/runtime/guild`), cos-bot and cos-scheduler (`cos_context.json`, `limits`), cos-agent-a (`mc-evidence`) |
| `model_gateway_receipts.jsonl` | Append-only record of model calls through the gateway | cos-bot, cos-scheduler |
| `research_dives/` | Earlier research dive output; not mounted by any current service, last changed in July | none |

Losing the EC2 disk today would lose all three.

## 2. Change (this PR)

- `scripts/backup_local.sh`: three more `rsync_path` lines, so each lands in
  the dated folder and reaches S3 at 03:00 and Dropbox weekly, like every
  other path. No other behaviour changes.
- `tests/test_backup_coverage.py`: every bind source under `data/` in
  `docker-compose.prod.yml`, across all services, must be covered by a
  backed-up path. A future mount that is not backed up fails the test. The
  test also checks that the guild binds and the receipts file are among the
  sources it found, so a parsing slip can't pass it silently.

The 02:00 UTC run can overlap Guild use (21:00 Chicago). The queue is written
by temp file and rename, so rsync copies a whole old or a whole new file; the
receipts file is append-only, so at worst the last line is partial. Both are
acceptable for a daily copy.

## 3. `german.bak-20260721T004344Z`: not in the daily backup

It is a root-owned copy of `german/` taken on 21 Jul and unchanged since.
Recommendation: **leave it out of the daily script** and let Robert decide on
a one-time archive.

- Adding it to the daily copy would store an unchanging folder again every
  day in S3.
- It is probably the same as `s3://…/2026-07-20/german/`, but that is not
  checked, and the S3 lifecycle rule expires that copy after 365 days.
- Next step, needs Robert's OK: a read-only compare against the 20 Jul S3
  copy. If it differs, or to keep it past the 365-day rule, one copy into a
  non-expiring archive prefix (a production write).

The same applies to three top-level files last written in June and July
(`curator_latest.json`, `curator_radar.json`, `curator_signals.json`): no
service mounts them and no tier copies them.

## 4. Found, not in this PR

Two production bind sources outside `data/` are in no tier either:
`/opt/minimoi/cos_memory.md` (CoS memory; cos-bot, cos-scheduler) and
`/opt/minimoi/agent_logs/` (portal, cos-agent-a; the installed
`backup_s3.sh` has no `agent_logs` step). They need their own small change
and a size check first.

## 5. Production steps (each needs Robert's approval)

1. Read-only size check of the three paths (and the items in 3 and 4).
   Sizes go to the private operator record, not this repo.
2. After #310 is merged and installed, install this `backup_local.sh` to
   `/opt/minimoi/scripts/` and check the next 02:00 run's log and the dated
   folder.
