# Vault meaning search (#660)

The Vault's Ask returns two lists: exact-word (`keyword`) and meaning (`meaning`) matches. Production and Dev served the keyword list only, because no embedder ran there. This adds the pinned embedder beside the Vault and turns both indexes (CoS's full index and MC's career-filtered index) into keyword-and-meaning indexes.

Build spec: planning `operations/OPS-2026-11-25-big-build/SPEC_LANE_E_v0.2.md`.

```
 vault-embedder (Ollama, nomic-embed-text@0a109f422b47, 768 dims; network minimoi-vault-embed, internal, no port)
   ▲ query vectors                    ▲ document vectors
 vault-ask ─ CoS                      vault-apply index step  ─► full index (meaning after the seed is promoted)
 vault-ask-mc ─ MC (tools door)       vault-apply seed (00:00–06:00 Chicago) ─► <vault>-work/meaning-seed/
                                      vault-mc-index ─► MC's index (borrows the full index's vectors; career filter first)
```

- **The embedder:** `docker/vault-embedder/Dockerfile`. Bases pinned by digest; the model is pulled at build and the build fails unless its manifest digest is `0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f`. CPU back ends only (~350 MB). Runs as uid 10001, read-only, no capabilities, 1 GiB and 1.5 CPU.
- **Identity check:** every client (`core/memory_index/embedder.py`) requires the full digest and, once per process, a known-answer vector (`core/memory_index/embedder_probe.json`, cosine ≥ 0.999). Another model, digest or quantization is refused (`pin_mismatch`); vectors from two models are never mixed (index and query checks).
- **Nothing changes until the seed is promoted:** while the serving index is keyword-only, `search` never calls the embedder.
- **The seed** (`vault_apply.py seed`): one time-boxed slice (default 420 s) per run, only between 00:00 and 06:00 Chicago, under apply's lock, into `<vault>-work/meaning-seed/`. Resumable record by record, and inside a big record batch by batch (see *Slices that overrun* below). **Only apply promotes it**, inside its ordinary single build and switch, before its acks. The seed folder goes after the inventory.
- **After promotion:** each apply copies the serving meaning index and embeds only the applied records' new text. An unreachable embedder fails that run (no switch, no ack; retried next run); the serving release keeps answering.
- **MC's index** turns meaning only once the full index of the same release is meaning. The career filter runs before any vector is taken: the donor is asked only for the kept chunks, and only kept text reaches the embedder. MC's index holds no career vector.

## Production activation (the owner's go)

Dependencies, in order:

1. **Disk:** at least 6 GB free on the Vault filesystem (`df -h /opt/minimoi`). Vectors add ~440 MB per index copy; three releases are kept.
2. **Image:** run the `Vault embedder image` workflow for the release commit on main. Write its digest to `/opt/minimoi/.env` as `MINIMOI_VAULT_EMBEDDER_DIGEST=sha256:…`; note the previous value (if any) in the deploy log for rollback.
3. **Start the embedder:** `docker compose -f docker-compose.prod.yml --profile vault up -d vault-embedder`; wait for `healthy`.
4. **Recreate the Ask services** with the release (they carry the new `MOI_OLLAMA_URL` and network): `vault-ask`, `vault-ask-mc`.
5. **Cron** (root; write the seed line for Chicago hours in the host's zone):
   ```
   */15 * * * *      … vault_apply.py --index-runner docker-container:minimoi-portal --meaning-embedder http://vault-embedder:11434 --embed-network minimoi-vault-embed run --execute
   7-59/15 0-5 * * * … vault_apply.py --index-runner docker-container:minimoi-portal --meaning-embedder http://vault-embedder:11434 --embed-network minimoi-vault-embed seed --execute
   ```
   MC's index cron: `--network minimoi-vault-embed` instead of `--network none`, and `--embedder-url http://vault-embedder:11434` after `build`.
6. **Job registry:** set `vault-meaning-seed`'s `active_from` in `config/scheduled_jobs.json` to the install time (it is a sentinel until then).
6a. **The one-time copy of the Mac's vectors** (the owner's decision, 26 Nov: one big copy instead of about 12 nights of server embedding). The Mac's own index is already a meaning index made with the pinned embedder (143,465 chunks, all with vectors, rebuilt nightly), and every production snapshot already carries it encrypted. So the copy uses the existing transport, and only the index object is fetched:
   - **Mac, in the batch window, before the first seed night:** publish a fresh snapshot with the existing job (`publish_production.py run --execute`, as in `core/memory_backup/production.py`'s runbook). Nothing new is installed by this: production keeps serving its own releases (refresh refuses `incremental_active`).
   - **EC2, as root:** `vault_apply.py seed-donor --execute`. It follows the pointer → COMPLETE → manifest chain, downloads only `index.sqlite.age`, checks the ciphertext hash, decrypts with the root-only identity and checks the plaintext hash. It then refuses unless the copy is a meaning index with the pinned digest, 768 dims, one vector per chunk and `quick_check` ok (`donor_not_pinned`, `donor_incomplete`). It lands as `<vault>-work/meaning-seed/mac-donor.sqlite` (root, 0600) with `DONOR.json`. It runs only in the window unless `--allow-outside-window` (the owner starting it by hand).
   - **The seed slices then borrow it.** Each chunk whose exact text the Mac already embedded for the same record takes the Mac's vector. Text production has that the Mac's copy lacks is embedded on the server as before. Only chunks production's own indexer keeps are looked up, so nothing the privacy rules hold enters the index.
   - **The check, every slice:** the first 16 borrowed vectors, then every 250th, are embedded again by the server's embedder and must agree (cosine ≥ 0.999; Dev measured 1.0 against the workstation). Any disagreement stops the slice, deletes the whole seed folder (copy included, so no borrowed vector survives) and fails `seed_failed:donor_mismatch` (alerting). The next slice then embeds on the server, the 12-night path. The evidence (`donor.vectors`, `donor.checked`) is in `SEED.json` and `plan`'s `meaning_seed`.
   - **Once the seed completes** the copy is deleted. The next apply promotes the seed as usual.
   - **Disk:** the encrypted and decrypted copy are about 0.7 GB each at peak (the ciphertext is deleted after decryption), plus the seed index. Keep the 6 GB from step 1.
   - **Expected time:** the borrowed path does chunking and keyword postings only. On the t3a.medium the Dev seed's borrowing runs (2,182 chunks reused) took about 10 s, so the production seed should finish in one or two slices on the first night, not about 12.
7. **Watch:** `vault_apply.py plan` shows `meaning_seed` (records and chunks embedded, last slice). When a slice ends `ok`, the next apply promotes it (`seed_promoted: yes` in the vault-apply status); MC's index follows on its next run (`"kind": "meaning"`).

**Checks after promotion:** `plan` shows `index_kind: meaning` and `index_embedder_pinned: true`; a CoS question worded differently from a note finds it; MC's `minimoi_vault_search` returns results labelled `meaning`.

**Rollback:** remove `--meaning-embedder`/`--embed-network` from the apply and MC cron lines and remove the seed line: the next apply rebuilds a keyword-only index by itself (a non-keyword serving index is reseeded keyword-only, as before #660). Stop `vault-embedder`; `search` never calls an embedder on a keyword index. An image rollback is the previous digest in `.env` and `up -d vault-embedder`.

## Slices that overrun (production, 26-28 Nov; fixed in the seed-resume PR)

What production showed: records of up to ~1,200 new chunks were embedded in one go, far past the 420 s slice. At 720 s the `docker run`
client was killed (`index_runner_failed`) but its container kept embedding and kept the seed index locked, so the next slice found
`index_busy`, and two clients on the 1.5-CPU embedder pushed batches of 16 past the 120 s request timeout (`embedder_unavailable`).

What the code does now:
- **Named containers.** The seed's index step runs as `minimoi-vault-seed`; apply's index steps as `minimoi-vault-index-<12 hex>`. A step
  that times out has its container removed (`docker rm -f`) before it fails `index_runner_timeout`. If docker still lists it, the step
  fails `index_runner_orphaned`: look with `docker ps -a --filter name=minimoi-vault-`.
- **Leftovers cleared first.** Each seed slice (under the vault lock, which every index step holds) removes any `minimoi-vault-seed` or
  `minimoi-vault-index-*` container before it starts, and reports `leftover_containers_removed` in `SEED.json`'s counts. A container it
  cannot remove stops the slice (`index_runner_orphaned`); a slice never runs beside an orphan. Containers from before this change have
  no name: check `docker ps` once before the first window after the deploy.
- **Resume inside a record.** New chunk text is embedded batch by batch and each batch is saved in the index's `pending_vectors` table
  before the next starts. The slice's budget is checked between batches, so a slice overruns by at most one batch, and the next slice
  carries on from the saved batches. A written or held record clears its rows; a run that visits the whole shelf clears the table.
- **Server batches.** Server-side embedders (the index step, MC's index build) send 8 texts per request; one request may take
  `max(120 s, 20 s x batch)` (160 s at 8; the workstation keeps 16 per request, now 320 s).

## Dev and staging (the AWS staging host)

- Build the image on the host: `docker build -t minimoi-staging/vault-embedder:pinned docker/vault-embedder`, then `docker builder prune -f` (the fetch stage downloads the 1.5 GB release tarball, checks its sha256 and unpacks only the CPU libraries).
- Start it from the staging compose (`vault` profile): `vault-embedder`.
- Dev feed: `dev_cycle.py … --embedder-url http://vault-embedder:11434 --embed-network minimoi-vault-embed` (the feed worker joins the network; each run borrows the last published index's vectors, so unchanged text is never embedded again).
- Dev Ask containers (`minimoi-dev-vault`, `minimoi-dev-vault-mc`): join `minimoi-vault-embed` with `MOI_OLLAMA_URL=http://vault-embedder:11434` and `MOI_OLLAMA_IDLE_KEEP_ALIVE=15m`.
