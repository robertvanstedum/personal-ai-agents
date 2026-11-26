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
- **The seed** (`vault_apply.py seed`): one time-boxed slice (default 420 s) per run, only between 00:00 and 06:00 Chicago, under apply's lock, into `<vault>-work/meaning-seed/`. Resumable record by record. **Only apply promotes it**, inside its ordinary single build and switch, before its acks. The seed folder goes after the inventory.
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
7. **Watch:** `vault_apply.py plan` shows `meaning_seed` (records and chunks embedded, last slice). When a slice ends `ok`, the next apply promotes it (`seed_promoted: yes` in the vault-apply status); MC's index follows on its next run (`"kind": "meaning"`).

**Checks after promotion:** `plan` shows `index_kind: meaning` and `index_embedder_pinned: true`; a CoS question worded differently from a note finds it; MC's `minimoi_vault_search` returns results labelled `meaning`.

**Rollback:** remove `--meaning-embedder`/`--embed-network` from the apply and MC cron lines and remove the seed line: the next apply rebuilds a keyword-only index by itself (a non-keyword serving index is reseeded keyword-only, as before #660). Stop `vault-embedder`; `search` never calls an embedder on a keyword index. An image rollback is the previous digest in `.env` and `up -d vault-embedder`.

## Dev and staging (the AWS staging host)

- Build the image on the host: `docker build -t minimoi-staging/vault-embedder:pinned docker/vault-embedder`, then `docker builder prune -f` (the fetch stage downloads the 1.5 GB release tarball, checks its sha256 and unpacks only the CPU libraries).
- Start it from the staging compose (`vault` profile): `vault-embedder`.
- Dev feed: `dev_cycle.py … --embedder-url http://vault-embedder:11434 --embed-network minimoi-vault-embed` (the feed worker joins the network; each run borrows the last published index's vectors, so unchanged text is never embedded again).
- Dev Ask containers (`minimoi-dev-vault`, `minimoi-dev-vault-mc`): join `minimoi-vault-embed` with `MOI_OLLAMA_URL=http://vault-embedder:11434` and `MOI_OLLAMA_IDLE_KEEP_ALIVE=15m`.
