# MC standing operations: runbook

**Covers:**
- the morning operations brief (`mc-standing-brief`);
- its optional Telegram copy;
- the nightly learning consolidation (`mc-learning-consolidation`).

**Spec:** standing operations v0.3.1, §§3, 7, 8a and 11.

**Code:**
- `core/mc_standing/`
- `scripts/mc_standing/`

**Status:** built and tested against fixtures. **Not activated.** Activation needs the checklist below and Robert's approval.

## What runs

| Job | When (Chicago) | Entry point | Default |
|---|---|---|---|
| Morning brief | 05:40 daily | `scripts/mc_standing/run_cycle.py` | off until activation |
| Control acknowledgement | every minute | `run_cycle.py --ack-control-only` | off until activation |
| Learning consolidation | 04:40 daily | `core/mc_standing/consolidation.py` (needs harness S3) | `MC_LEARNING_CONSOLIDATION_ENABLED` off |

**Before activation:**
- Registry entries for both scheduled jobs wait in `config/mc_standing/scheduled_jobs.candidate.json`. Merge them into `config/scheduled_jobs.json` at activation, with `active_from` set to the first run.
- Run the brief with `--jobs-root`, so it writes the standard job status file.

## The durable spool (the worker's only writable state)

`--state-root` holds one JSON file per record, under five folders:

| Folder | Holds | Changes? |
|---|---|---|
| `observations/` | sanitized observations per cycle and watch | immutable |
| `findings/` | finding streams; learning dispositions (revoked or missing sources, held-run decisions) | immutable |
| `cycles/` | cycle records, `control-ack`, `worker-status`, `learning-consolidation` state | replaced atomically |
| `outbox/` | brief and learning events; the Telegram sending ledger | events immutable; ledger replaced |
| `delivery/` | rendered brief, Telegram message, learning results and payloads | immutable |

**Properties:**
- **Permissions:** files are 0600 and folders are 0700.
- **Symlinks:** refused anywhere in the tree.
- **Writes:** temp file, fsync, rename, then a directory fsync.

**A full disk fails closed.** The restart converges to:
- one brief;
- at most one Telegram message;
- no false "delivered".

`tests/mc_standing/test_disk_full.py` sweeps every write point to prove this.

## Backup and restore test (an activation prerequisite)

```bash
python3 scripts/mc_standing/backup_spool.py backup --state-root /path/to/state --out /path/to/backups
```

```bash
python3 scripts/mc_standing/backup_spool.py verify --archive /path/to/backups/mc-standing-spool-YYYYMMDDTHHMMSSZ.tar.gz
```

**What `backup` does:**
- waits for the cycle lock, so it never captures a cycle that's in flight;
- excludes temp files;
- writes a 0600 archive plus a `.sha256` file.

**What `verify` does:**
- checks the digest;
- refuses unexpected archive members;
- restores into a private temp directory;
- reads every record through the spool's reader.

**Output:** counts only, never content.

## States that need a person

| Where | State | Meaning | What to do |
|---|---|---|---|
| Brief cycle | `delivery_unknown` | The post may have reached the platform | Nothing. The next run reads back by event ID and resends only if the post is verifiably absent |
| Brief cycle | `held` (`send_window_closed`) | The brief wasn't sent before 09:00 on its own date | Read it on the site if it is useful. It is never sent late; the next day is a new cycle |
| Brief cycle | `failed` (`ingress_refused`, `outbox_missing`) | The platform refused it, or the outbox is broken | Check the ingress binding and token, then the spool |
| Telegram ledger | `uncertain` | The message may have gone out | Look in Telegram. It is **never resent automatically** |
| Telegram ledger | `not_sent` | Certainly not delivered (rejected, refused, or the window closed) | Fix the binding or token. The site brief is unaffected |
| Consolidation | `held` (`model_call_outcome_unknown`) | Spend or output unknown after a crash or error | Check the gateway's reservation and usage. Then run `clear_held(spool=…, claims=…, operator="robert", disposition="release")`, which is recorded; the sources become eligible again, and a later run may pay again |
| Consolidation | `invalid_snapshot` | The eligibility snapshot is out of commit order | This is a harness fault. Nothing advances |
| Job status | `warn` / `failed` | See `summary` (fixed phrases) | `paused by owner` is expected while paused |

## Pause, resume and emergency stop

**Pause or resume:** owner-authenticated, through the platform's control endpoint, which writes `mc-standing-control/1`.
- A running cycle acknowledges at its next checkpoint.
- The every-minute `--ack-control-only` acknowledges only when no cycle holds the lock.
- A missing or unreadable control document means **paused**.

**Emergency stop:** create the disable file (`--disable-file`). Its existence alone stops reads, model calls and sends at the next checkpoint. It doesn't depend on the endpoint, and it doesn't require removing the scheduler.

**What pause doesn't do:**
- It doesn't retract a delivered brief.
- It doesn't backfill missed days on resume.

## Activation checklist (§11)

1. Back up the spool root, then run `verify` on the archive.
2. Verify source grants and real freshness for every approved watch (the watchlist needs Robert's approval).
3. Verify a single scheduler and the time zone (America/Chicago), and that no other copy is scheduled.
4. Verify the budget, or that synthesis is disabled. P0 makes no model call.
5. Do one real site delivery to the approved MC Operations conversation, plus the kill and pause checks.
6. Confirm the existing scheduler and reminder jobs are unchanged and still running.
7. Review the container's resource envelope; compare the per-cycle `resources` in cycle records with the approved one.
8. Get an independent dev gate and Robert's approval of channel, time, coverage and the exact rollout diff.
9. Merge the candidate registry entries, with `active_from` set.

## Rollback

1. Create the disable file (immediate).
2. Remove the scheduled trigger, and restore the previous code and config.
3. **Do not delete** observations, findings, delivery holds or ledgers.
4. Reconcile anything in flight:
   - read back `delivery_unknown` events by ID;
   - leave Telegram `uncertain` as it is;
   - leave consolidation `held` for a decision.
5. Disabling the job is not authority to retry unknown effects.
6. A restart must not flood: only today's cycle can send, and only before 09:00.
