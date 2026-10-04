# Memory watch: operator page

**Dev only (Mac).** Nothing here touches production. You can pause it at any time and nothing is lost: the sources
stay where they are, and the next run picks up everything that changed.

## What it does
`memory-watch` copies your approved agent session files (Claude Code, Codex) into the memory shelf
(`~/minimoi-staging/data/memory`), scrubbed, as editions: one record per session, never overwritten. It runs at **04:00
daily** (launchd `com.vanstedum.minimoi-memory-watch`), then the **09:00 watchdog** tells you on Telegram if it failed,
never ran or got stuck. The inbox (claude.ai, Grok, ChatGPT exports) is **never** run by the job; that stays a hand step.

`moi watch <source>` is exactly what the job runs for each source, so running it by hand is the same thing.

## Approval gate (typed yes, once per source)
A source captures only after you approved its dry run. Until then the job lists it `not_approved` and does not read it.

```bash
scripts/moi dry-run codex           # counts only: how many files, bytes, dates. Reads no content.
scripts/moi approve-source codex    # you type yes at the terminal (there is no --yes)
```

The approval is tied to the source's folder and `never_copy` list. Change either and it goes `stale` (the job warns;
approve again). Normal new sessions never need a new approval.

## Install, pause, remove
The plists name the main checkout; until this work is merged to `main`, install from the integrated worktree and **do not
remove `~/.worktrees/memory-dev` while the job is installed**. The Python interpreter stays the main checkout's `venv`.

```bash
MINIMOI_REPO=~/.worktrees/memory-dev scripts/jobs/launchd.sh install     # both jobs: 04:00 watch, 09:00 watchdog
scripts/jobs/launchd.sh status                                           # loaded or not, plus the last state of each job
launchctl bootout gui/$(id -u)/com.vanstedum.minimoi-memory-watch        # pause just the watch (install brings it back)
scripts/jobs/launchd.sh uninstall                                        # remove both (status files and logs stay)
```

## Run it now, and read the result
```bash
venv/bin/python scripts/memory/daily_watch.py                            # the whole job, with its status file and alert rules
launchctl kickstart gui/$(id -u)/com.vanstedum.minimoi-memory-watch      # the same, run by launchd
scripts/moi watch codex                                                  # one source only (prints: status and counts)
scripts/moi ledger                                                       # captured vs excluded vs missing, and the coverage line
scripts/moi doctor                                                       # read-only: does every record agree with its own editions?
cat ~/minimoi-staging/data/jobs/memory-watch.json                        # last result: state, times, one code per source
tail ~/minimoi-staging/logs/memory-watch.log                             # what launchd captured
```
`scripts/moi` needs the project venv (`MOI_PYTHON=venv/bin/python`) if you call it from a worktree.

States: **ok**; **warn** (a source not approved or stale, disk low, an unstable or failed file, or a **coverage flag**: the
reader took fewer messages than a file shows, or met a format it has not seen); **failed** (one Telegram message, exit 1).
A damaged record body is repaired with `scripts/moi repair` (you type yes; the old file is kept, editions are never touched).

## The capture report (what the Operate matrix shows)
After the captures the job publishes `memory-capture-report.json` and `memory-capture-matrix.json` under the jobs root, and records a small fidelity sample.
On demand, read-only, without recapture:
```bash
scripts/moi report                    # one summary line, writes nothing
scripts/moi report --json             # the full counts-only payload
scripts/moi migrate-preview           # what the current parser rules would change in each provider's records (counts only)
```
Contract, states and rules: `docs/memory_capture_report_contract.md`.

## Counts only
Every status file, log line, ledger row, review item and Telegram message carries ids, counts and fixed codes: never a
title, a file name or a word of a conversation. Keep it that way when you check on it: `moi list` (titles) shows them only
at your own terminal, and nothing in this page needs you to open a record.

Registry and status format: `docs/jobs_status_contract.md`. Job table: `OPERATIONS.md` ("Scheduled jobs and launchd on the Mac").
