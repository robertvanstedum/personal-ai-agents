# Spec — MiniMoi 1.2 data map, private sync and backup classes, v1.0

**11 Nov 2026 · Claude Code · approved by the owner, 11 Nov 2026 · part of MiniMoi 1.2.**
Dev first. Every production step needs a tracking issue and the owner's word for an exact commit.

This is the public spec of the solution: what is built, where it lives and how it is organized. The owner's
instance record (which private folders are published, the inventory, restore receipts) lives in the private
planning repo, in the 1.2 data migration spec of the same date, which points here. The rule between the two is the
platform's own: **capability is public; the instance is private.** No personal content, file names or owner words
appear here or in any file this spec creates in this repo.

Supersedes in part: `spec_production_backup_2026-07-05.md` (its scope and backup classes; its three tiers stay).

## 1. Problem

Private material that the agents need in production (the owner's career workspace, private specs and plans) lives
only on the owner's Mac, so production CoS and MC cannot read it, and nothing defines how it reaches production or
stays consistent. Separately, several production stores have no daily backup, personal and secret bytes leave the
host unencrypted (S3 daily, Dropbox weekly), and some data has two copies with no defined owner.

The bar: a reader one year from now can tell, for every kind of data, what it is, where it lives (Mac, AWS Dev,
production), who writes and reads it, how it is backed up and restored, and how copies stay consistent, from the
data map alone.

## 2. Principles

1. Personal data never goes on GitHub. This repo holds code, schemas, templates, fixtures and the content-free map.
2. One canonical home and one writer per kind of data. Every other copy is a **mirror** (read-only, rebuildable) or a
   **backup** (never read by a service). Never two writable "current" homes for one file.
3. Files first; Postgres only on real need.
4. Read-only first. Agent write access to private sources is a later, separate build through one controlled writer.
5. Sync is not backup. Save, commit, publish, apply and recall are separately verified events, each with a receipt.
6. Encryption with `age` before personal or secret bytes leave a host. Private keys live in SSM (production) or the
   Keychain (Dev and Mac), never in a bundle, a backup or a container.
7. Dev before production; plan on dependencies, not dates; name a cause only with a log.

## 3. Organization

### 3.1 Kinds of data

| Kind | Canonical home | Writer | Mirror | Backup |
|---|---|---|---|---|
| Owner's private sources (career workspace) | Mac private folder (configured, outside every repo) | The owner and their Mac tools | Production `P/private/professional/releases/<hash>/`, ro | Encrypted career-sync objects, plus the owner's local copy |
| What CoS files back for the owner | `P/private/professional-inbound/` until pulled | cos-scheduler only | Mac `00_from_minimoi/<date>/` after pull | `daily-encrypted` |
| Private specs and plans | Private planning repo on GitHub | Agents and owner, through git | `P/private/planning/`, ro | GitHub and clones |
| CoS, MC and platform runtime | Production | Their services | None | Per class (section 5) |
| Vault shelf | Mac shelf | Mac capture and index jobs | `P/vault/current` | Encrypted S3 |
| Repo files under `data/` | This repo, seeds and fixtures only | Developers | None | Git |

P is `/opt/minimoi` on production and `/opt/minimoi-staging` on AWS Dev.

**Recorded exception X1:** cos-agent-a mounts all of `P/data` read-write (the 13 Oct shared-access experiment). The
map names it and its writable mounts; ending it is its own step.

### 3.2 Production layout for private material

```
/opt/minimoi/private/                 never mounted into any container
  DATA_MAP.md                         generated, private detail
  professional/                       stable bind source; owned by the reader uid, 0700
    releases/<manifest-hash>/         one real directory per applied bundle; current and one previous kept
    current -> releases/<hash>        flipped only after every hash checks
    quarantine/<bundle-id>/           failed decrypt or hash; never read
    RECEIPT.json                      last apply: bundle id, manifest hash, file count, time
  professional-inbound/               cos-scheduler's uid, 0700; the only writable private area
    uploads/<date>/<file_id>/         copy of an owner upload, with meta.json
    drafts/<date>/...                 CoS outputs for the owner
  planning/                           private planning mirror, ro, refreshed by a host timer
  judgment/                           owner judgment store; never mounted
```

It sits outside `P/data` so X1 cannot reach it. `domains/cos/work/roots.py` requires the Work process to own each
source root with mode 0700 and drops any path containing a symlink, so the release tree is owned by the reader uid
and `COS_WORK_SOURCE_ROOTS` names the real `releases/<hash>/…` path, never `current`. If cos-scheduler and
cos-agent-a run as different uids, the applier writes a second copy of the release owned by cos-agent-a's uid; the
Work reader's check is never loosened.

| Container | Mount | Mode |
|---|---|---|
| cos-scheduler | `P/private/professional` → `/minimoi/private/professional` | ro |
| cos-scheduler | `P/private/professional-inbound` → `/minimoi/private/professional-inbound` (`COS_WORK_ROOT`) | rw |
| cos-agent-a | `P/private/professional` (or its own-uid copy) → `/minimoi/private/professional` | ro |
| cos-agent-a, mc-agent | `P/private/planning` → `/minimoi/private/planning` | ro |

What is visible inside the mirror is decided by the **publish set** (a private config on the Mac, not in this repo).
The first publish set is the current-resume folder only, class `robert_source`, plus private specs. Widening it is a
publish-set change on the owner's word, recorded in the private map.

### 3.3 How CoS and MC use the data

This is the behaviour the layout exists for. Each step leaves a receipt, so what an agent read or wrote can be checked
afterwards.

| Who | Reads | When | Does with it | Writes |
|---|---|---|---|---|
| CoS chat (cos-agent-a) | The current release of the owner's sources, ro (first: the current resume) | The owner asks about career work: the resume, a cover letter, a target | Answers from the file itself instead of asking for an upload; names the file, its sha256 and the release it read; says when a file is dirty (not yet committed on the Mac) | Nothing in `P/private`; its own outbox and scratch as today |
| CoS Work (cos-scheduler) | The same release, through `COS_WORK_SOURCE_ROOTS`, each root labelled with one context class (`robert_source`, `external_source`, `agent_draft`, `coauthored_output`) | Career tasks and briefings | Keeps the owner's own words apart from postings and drafts by class; a reader receipt per file read | Inbound: copies of uploads the owner marks as career material, and CoS career drafts, each with `meta.json` |
| CoS chat and MC (mc-agent) | The private planning mirror, ro | The owner asks about a spec, plan or decision | Read the governing document by path and cite it | Nothing |
| MC | — | A document lives somewhere it cannot reach (the Mac) | Says so in its first step and offers to ask Claude Code on the Mac, instead of searching public places | — |
| The owner on the Mac | Inbound, after `pull` | When convenient | Files what is worth keeping into the real folders and commits; the next publish brings it back to production | The canonical sources |

CoS never edits a source. What it produces stays labelled as an upload or draft until the owner files it. A later,
separate build may give CoS a controlled writer for career artifacts; until then this is read-only by design.

## 4. career-sync

Code in this repo: `scripts/operations/career_sync/` (Mac commands `publish` and `pull`, host commands `apply` and
`pack-inbound`), with tests against fixture trees. All paths and recipients come from config; none are hard-coded.

**Mac → production.** `publish` builds a tar of the publish set, excluding `.git`, Office lock files, `.DS_Store` and
a `never_copy` list. `MANIFEST.json` records per file: path, sha256, size, mtime and a dirty bit (uncommitted or
untracked in the workspace's git repo), plus that repo's `HEAD`. The tar is `age`-encrypted to the host recipient and
uploaded as `career-sync/<manifest-hash>.tar.age`: content-addressed, so an unchanged tree stores no new object. Dev
uses a Dev bucket; production uses `minimoi-backups` only after its lifecycle is fixed (section 7, S8).

**Apply on the host** (on demand through SSM; a batch-window timer is optional): download, decrypt into
`releases/<hash>.partial/`, verify every sha256, rename, set owner and mode, flip `current`, rewrite
`COS_WORK_SOURCE_ROOTS`, restart cos-scheduler only, write `RECEIPT.json`, prune to current plus one previous. A
failure quarantines the bundle and leaves `current` unchanged. Rollback is flipping `current` back.

**Production → Mac.** When the owner marks an upload as career material, or CoS's career task files an output,
cos-scheduler copies it into inbound with `meta.json` and a receipt; the original stays in `P/cos/inbox`.
`pack-inbound` encrypts unpulled items to the Mac recipient. `pull` places them under `00_from_minimoi/<date>/`,
never overwriting (a clash gets a suffix), and acknowledges them. Nothing in inbound is canonical until the owner
files it on the Mac; the next publish carries it back.

**Rules:** publish refuses while an inbound bundle is unpulled; publish warns on dirty files; production never edits
a release; CoS cites the file sha256 and the manifest hash, and a git commit only for a clean file.

## 5. Backup classes

| Class | Meaning | Restore test |
|---|---|---|
| `daily` | In `backup_local.sh`, then S3 daily and Dropbox weekly | One file and one folder to scratch; hashes compared |
| `daily-db` | Database dump safe to hold in plaintext | Into a throwaway Postgres; row counts compared |
| `daily-encrypted` | Personal or secret bytes, `age`-encrypted **inside the local dated folder**, so no off-host tier ever sees plaintext | Decrypt with the SSM identity into scratch; diff or counts |
| `mirror` | Rebuilt from its canonical home | Rebuild |
| `regenerable` | The code rebuilds it | None |

`daily-encrypted`: `personal_agents` and `litellm_keys` dumps, the OpenClaw named volumes (tarred read-only, then
encrypted), `P/cos/inbox`, CoS store, conversations and personal notes, `P/auth`, `P/private/professional-inbound`,
`P/private/judgment`, secrets files. `daily` (new or confirmed): CoS outbox and planning-studio scratch, `P/state/1-2`,
MC working folders, records, Guildhall journals, runtime, vault-log, agent logs, and the existing domain data.
`mirror`: `P/private/professional`, `P/private/planning`, `P/vault/current`. Restore drills run per class, Dev then
production, into scratch only.

## 6. The data map

1. **`config/data_map.json`**: one entry per store — id, host path per host, container mounts and modes, writer,
   readers, backup class, restore test, canonical or mirror, exceptions, capabilities (Docker socket), governing
   spec. No content and no private file names.
2. **`scripts/operations/data_map_check.py`** in CI, extending `tests/test_backup_coverage.py`. It fails when:
   1. a host path mounted in any `docker-compose*.yml` is missing from the map;
   2. a store with a daily class is not copied by the backup scripts;
   3. a path the backup scripts copy is missing from the map;
   4. a `daily-encrypted` store is copied without the encrypt step;
   5. a `P/private` path is mounted rw anywhere but inbound on cos-scheduler;
   6. a `P/private` path sits under `P/data`, or `P/private` itself is mounted;
   7. a container mounts a parent rw over more than its named stores (X1 excepted by name);
   8. a container with a private ro mount has a writable mount not named in the map;
   9. a new service mounts private data and the Docker socket.

   Each rule has a bad-compose fixture that proves it fires.
3. **Generated on deploy:** `P/DATA_MAP.md` (stores, classes, latest drill pass/fail date) and
   `P/private/DATA_MAP.md` (the same plus private detail).

## 7. Steps, in dependency order

| Step | What | Waits on |
|---|---|---|
| S1 | Read-only inventory (Mac, Dev, production): reader uids, volumes, crontab and tier logs, bucket settings, publish-set size | Approval (done) |
| S2 | Data map, CI check, generated pages | S1 |
| S3 Dev | Backup closure and the encrypt step; one restore drill per class | S2 |
| S4 Dev | career-sync publish and apply, private layout, mounts, Work root | S1, S3 |
| S5 Dev | Inbound filing and pull | S4 |
| S6 Dev | Planning mirror timer and mounts; MC says when a document is out of reach instead of searching | S1 |
| S8 | Bucket lifecycle: keep current versions, expire noncurrent after 90 days; confirm encryption and versioning | S1 |
| S7 | Production: S3, then S4–S6, each on the owner's word for an exact commit | Dev acceptance; S8 before S4 |
| S9 | Per-path retirement of `scripts/sync_private_repo.sh`; repo `data/` files become seeds | S1, S7 |

## 8. Acceptance

Every test passes on a receipt or a tool result, never on model prose: a canary line that exists only in the target
file must come back, and the receipt's sha256 must match the release. T1: fresh CoS chat reads the current resume
with no upload. T2: MC reads a private spec from `/minimoi/private/planning` within a minute; with the mount removed
it says so in its first step. T3: a new publish is cited by the new manifest hash. T4: an upload pulled to the Mac
keeps its sha256 and overwrites nothing. T5: same-hash publish adds no object; unpulled inbound refuses publish; a
bad bundle is quarantined and `current` does not move. T6: restore drill per class. T7: every CI rule fires on its
fixture. T8: next-day CoS cites release and inbound from receipts.
