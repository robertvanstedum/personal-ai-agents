# Runbook — Private data: where it lives, how it moves, how CoS and MC use it

**11 Nov 2026 · Claude Code.** Operations companion to `docs/specs/spec_1_2_data_map_and_private_sync_v1.0_2026-11-11.md`
(the design) and `scripts/operations/career_sync.py` (the tool). Tracking: #502. The owner's instance record
(which folders are published, inventory, restore receipts) is in the private planning repo, 1.2 data migration.

The rule behind everything here: **capability is public, the instance is private.** This page names folders by
role, never by content. Nothing personal is in this repo.

## 1. One picture

```
 OWNER'S MAC (canonical)                       S3 (ciphertext only)                     HOST (Dev, then production)
 ───────────────────────                       ────────────────────                     ───────────────────────────
 ~/minimoi-private/professional/                                                         <P>/private/                 0711 root, never mounted
   career/<career workspace>/   (local git)                                                .work/                     0700 apply scratch
     ACTIVE_CV/       ← publish set                                                        professional/              0700 root (uid 0)
     03_specs/        ← publish set   ──publish──►  <bucket>/career-sync/                    releases/<hash>/tree/…   files 0400
     00_from_minimoi/ ◄──pull (S5)──┐               <manifest-hash>.tar.age  ──apply──►      current -> releases/<hash>
     (everything else stays on the Mac)             latest.json                               RECEIPT.json
                                    │                                                          work_source_roots.json
 ~/minimoi-private/career-sync/     │               <bucket>/career-inbound/ (S5)            professional-agent-a/      0700 uid 1000
   config.dev.json, config.prod.json└────────────── <bucket>/career-inbound-acks/              releases/<hash>/tree/…   (same release, own copy)
   acks.dev/, acks.prod/                                                                       current -> releases/<hash>
 ~/minimoi-private/sync-copies/  (pre-migration copy)                                          RECEIPT.json
                                                                                           professional-inbound/      (S5, cos-scheduler only)
                                                                                           judgment/                  never mounted
                                                                                         <P>-tools/age-v1.3.2/        pinned age + PIN.json
                                                                                         <P>-tools/career-sync/       career_sync.py + host.<env>.json
                                                                                         <P>-keys/career-sync.agekey  0400 root, never leaves the host
```

| | Dev (AWS staging) | Production |
|---|---|---|
| `<P>` | `/opt/minimoi-staging` | `/opt/minimoi` |
| tools / keys | `/opt/minimoi-staging-tools`, `/opt/minimoi-staging-keys` | `/opt/minimoi-tools`, `/opt/minimoi-keys` |
| `<bucket>` | `minimoi-dev-private-sync` (versioned, SSE-S3, noncurrent 90 days) | `minimoi-backups` (versioned; current kept, noncurrent 90 days) |
| Reached by | SSM to the staging instance | SSM to the production instance |

## 2. Who writes, who reads

One writer per kind of data. Everything else is a read-only mirror or a backup.

| Data | Writer | Readers | Mode inside the container |
|---|---|---|---|
| Career sources (canonical) | The owner on the Mac | — | — |
| `releases/<hash>/` and `current` | `career_sync.py apply`, root, on the host | cos-scheduler (`professional/`), cos-agent-a (`professional-agent-a/`) | read-only bind at `/minimoi/private/professional` |
| `work_source_roots.json` | `apply` (and `rollback`) | cos-scheduler via `COS_WORK_SOURCE_ROOTS_FILE` | read-only |
| `<bucket>/career-sync/*` | The Mac (`publish`) | The host role (read only) | — |
| `<bucket>/career-inbound/*` (S5) | The host | The Mac (`pull`) | — |
| `professional-inbound/` (S5) | cos-scheduler only | the host packer | rw in cos-scheduler only |
| Private specs and plans | git (private planning repo) | MC and CoS through the planning mirror (S6) | read-only |

Each side's cloud access is denied writes to the other side's prefix, and nobody can delete.

## 3. How the agents use it

| Agent | What it does | Where it looks | What it must not do |
|---|---|---|---|
| **CoS chat** (cos-agent-a, uid 1000) | Answers resume and career questions from the files instead of asking for an upload. Names the file and the release (`manifest_hash` from `RECEIPT.json`). Says when the owner describes something different from the file: the Mac copy is canonical. | `/minimoi/private/professional/current/tree/ACTIVE_CV/`, `/minimoi/private/professional/RECEIPT.json` | Copy the files into the outbox, Planning Studio, `/minimoi/data` or any writable folder; quote more than asked |
| **CoS Work** (cos-scheduler, uid 0) | Reads the source root `career/active_cv` as class `robert_source`, so the owner's own words are never confused with postings or drafts | The path in `work_source_roots.json` (the real release folder; `roots.py` refuses any path with a symlink) | Write anywhere in the mirror (the mount is read-only) |
| **MC** (mc-agent) | Reads private specs from the planning mirror (S6). When a document is somewhere it cannot reach, it says so in its first step and offers the Mac route | `/minimoi/private/planning` (S6) | Search public places for private documents until the time limit |
| **Claude Code on the Mac** | Runs `publish` (and later `pull`) on the owner's word; prepares hosts through SSM operators with a rollback each | the Mac config files | Edit files in the canonical workspace other than what the owner asks |

The CoS guidance is the paragraph about career sources in `docker/cos-agent-a/COS_AGENTS.md`.

## 4. Everyday operations

All commands run from the repo root on the Mac; host commands run through the SSM operator as root.

**Publish after editing the resume (Mac):**

```bash
python3 scripts/operations/career_sync.py publish --config ~/minimoi-private/career-sync/config.prod.json --dry-run
```

```bash
python3 scripts/operations/career_sync.py publish --config ~/minimoi-private/career-sync/config.prod.json
```

The dry run shows the file count, the number of uncommitted ("dirty") files and the release hash. The real publish prints
`stored_new_object: false` when nothing changed. Commit in the career workspace first if you want CoS to cite a commit.

**Apply on the host:** `python3 -I -B <P>-tools/career-sync/career_sync.py apply --config <P>-tools/career-sync/host.<env>.json`.
Then restart only cos-scheduler, so CoS Work loads the new roots file. cos-agent-a follows `current` without a restart.
The result is `applied` (with `previous` and `pruned`) or `unchanged`.

**Check:** `career_sync.py status --config …` shows each reader's current release, the retained releases, the quarantine count and the
last receipt.

**Roll back:** `career_sync.py rollback --config …` checks and points `current` back at the release that was current before
(or `--to <hash>`), for both readers, and rewrites `RECEIPT.json` (with `result: rolled_back`) and the Work roots file,
so CoS names the release it actually reads. Restart cos-scheduler.

## 5. What can go wrong

| Symptom | Meaning | Action |
|---|---|---|
| `refused: inbound_not_pulled` on publish | The host filed something back that the Mac has not pulled (S5) | `pull` first |
| `refused: ciphertext_hash_mismatch` or `decrypt_failed` on apply | The object is damaged or was not encrypted to this host | The bundle is in `quarantine/`; `current` did not move. Republish from the Mac |
| `refused: tree_does_not_match_manifest` / `file_hash_mismatch` | The bundle content does not match its manifest | Same as above |
| `refused: symlink_in_publish_set` | A symlink is inside a published folder | Replace it with the real file or move it out of the publish set |
| CoS asks for an upload of a published file | The guidance or the mount is missing in that container | `docker exec minimoi-cos-agent-a ls /minimoi/private/professional/current/tree`; check the image has the career-sources paragraph |
| CoS Work reports `source_root_unavailable` | The roots file points at a missing, non-owner-private or symlinked path | `status`; re-run `apply`; restart cos-scheduler |
| A container restarts with `exec … Permission denied` after an image layer | The layer's folders were not 0755 | Rebuild with the folders 0755 (`--no-cache`) |

## 6. Backups and recovery

- Every bundle in S3 is encrypted to the host key **and** to the Vault backup key, so it also opens with the
  production SSM identity if a host key is lost. The bucket is versioned, and nobody can delete.
- The host keeps the current release and the one before it. Older releases are rebuilt from S3 or the Mac.
- Losing a host key means: generate a new one, put its public recipient in the Mac config, republish.
- Before the first publish, the publish set was copied with checksums to `~/minimoi-private/sync-copies/`.
- The wider backup closure (encrypt personal stores before any off-host tier; restore drills per class) is S3 of the
  spec, tracked separately.

## 7. Status (11 Nov 2026)

| Step | Dev | Production |
|---|---|---|
| Host prepared (age, key, `private/` layout, tool) | done | done |
| Bucket and access | done | done (D7 lifecycle applied) |
| First publish and apply | done; all 44 files hash-identical | done; same combined hash as the Mac and Dev |
| Mounts and CoS guidance | done (operator overlay) | deployed in `7acb3037` (PR #501) |
| T1: CoS reads the resume with nothing uploaded | passed | passed |
| Inbound filing (S5), planning mirror (S6), data map (S2), backup closure (S3) | next | after Dev |
