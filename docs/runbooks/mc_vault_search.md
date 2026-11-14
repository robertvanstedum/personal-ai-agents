# MC searches the Vault (#594)

Design: planning `initiatives/INIT-2026-0007-interaction-vision/partner-interaction-2026-11-13/DESIGN_MC_VAULT_SEARCH_2026-11-14.md`.

```
 shelf ─┬─► full index (vault-apply / dev feed) ──► vault-ask       ◄── CoS hook (grant production-mc)      unchanged
        └─► MC index (career filter, index time) ─► vault-ask-mc    ◄── portal tools door ◄── MC: minimoi_vault_search
```

- **MC index:** `core/memory_index/mc_index.py` builds it with `core/memory_index/career_filter.py`. Generic rules live in `config/vault/career_filter.default.json`; each host adds a **private** rules file (root 0600, never in git) with names: employers, recruiters, people, target roles.
- **vault-ask-mc:** `core.memory_ask.service --require-profile mc`. It answers only from an index whose metadata names `profile=mc` and a filter digest; anything else gets `503 index_unfiltered`. Its registry `agents-mc.json` holds only grant `mc`.
- **The tool:** `minimoi_vault_search` (tier 1, read, taints the turn) runs in the portal (`core/mc_tools/vault_search.py`). It needs `MINIMOI_MC_VAULT_ASK_URL` (internal `http://<container>:<port>` only) and the token file `MINIMOI_MC_VAULT_ASK_TOKEN_FILE` (default `/run/secrets/mc/vault-ask-mc-token`, owner-only).

## Private rules file

Same schema as the default file: `{"schema": "minimoi.career-filter.v1", "terms": [...], "exclude_providers": [], "exclude_records": []}`.

- **Terms** are regex fragments, matched case-insensitively as whole words.
- **Merging with the default file:** lists are united, `record_threshold` and `record_fraction` take the lower value, `turn_window` the wider.
- **Permissions:** root-owned, mode 0600. A file readable by group or others is refused (`filter_insecure`).
- **CoS's conversations are excluded by default** (Robert, 14 Nov: "better to restrict now and open later if needed"). The default file has `"exclude_providers": ["minimoi-cos"]`. To open them to MC later, set it to `[]`: a one-value edit, no code. The new digest forces a full rebuild on the next run.

## Dev bring-up

1. **Images:** build the portal image and MC's image from this commit. MC's image carries the new `openclaw.json` and the plugin's `tools.json`.
2. **Feed:** add `--mc-index-path <data>/memory-index/memory-mc.sqlite` to the `dev-memory-feed` run, plus `--career-rules-private <file>` if Dev has a private rules file. The feed report's `mc_index.code` should read `ok`. A failed MC build never fails the feed.
3. **MC's Ask:** start a second Dev vault container from the same image and mounts as `minimoi-dev-vault`, with `-e DEV_VAULT_PROFILE=mc`, named `minimoi-dev-vault-mc`. It serves port 8772. Put it on a network the **portal** is on and MC's agent is not, for example `minimoi-staging_default`.
4. **Grant:** run this at an interactive terminal; it refuses under a pipe or script:
   `docker exec -it minimoi-dev-vault-mc python -m core.memory_ask.agents_cli --registry /vault/memory-ask/agents-mc.json add mc --raw`.
   Write the printed token to the file the Dev portal sees as `/run/secrets/mc/vault-ask-mc-token`, mode 0600, owned by the portal's user.
5. **Portal:** set `MINIMOI_MC_VAULT_ASK_URL=http://minimoi-dev-vault-mc:8772` in the Dev portal's env.
6. **Policy:** Dev's installed policy needs the `vault_search_v1` rule (copy it from `services/mc_operations/policy/mc-policy.example.json`) plus a fresh authority record.
7. **Restart:** recreate the portal and restart `mc-agent`. Availability should show `minimoi_vault_search` enabled.

## Dev checks

1. **MC cites source and date.** Ask MC something only the Vault knows, for example a topic from a Dev MC or CoS conversation. MC calls `minimoi_vault_search` and cites the source and date. The portal log shows `mc tool minimoi_vault_search -> done/vault_read`.
2. **Career material stays out of MC's reach.** In a Dev **MC** conversation, write a career line between two ordinary ones, for example "rewrite my resume for the recruiter call". After the next feed run (5 minutes):
   - CoS's Ask finds it: `minimoi-dev-vault`, grant `production-mc`.
   - MC's search does not find it: ask MC to search "recruiter resume", and the result is empty. A line two or more turns away from the career line is still found.
   - Directly: `POST http://minimoi-dev-vault-mc:8772/v1/ask` with MC's token returns no passage.
3. **CoS's conversations are not in MC's index.** A phrase from a Dev CoS conversation is found by CoS's Ask and not by MC's.
4. **CoS's search is unchanged.** Run the same query against `minimoi-dev-vault` before and after this release; the results are identical.
5. **The profile guard works.** Point `minimoi-dev-vault-mc` at the full index by mistake (`--index .../memory.sqlite`). Every request answers `503 index_unfiltered`.

## Production activation (on Robert's word; production writes)

1. **Folders:** `install -d -m 0700 /opt/minimoi/vault-mc /opt/minimoi/vault-mc/index /opt/minimoi/vault-mc/memory-ask`. Write the private rules file `/opt/minimoi/vault-mc/career_filter.private.json` (0600).
2. **First build,** then cron (root, every 15 minutes, offset from vault-apply):
   ```
   7,22,37,52 * * * * docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges:true --tmpfs /tmp:size=16m -v /opt/minimoi/vault:/vault:ro -v /opt/minimoi/vault-mc:/vault-mc "$(docker inspect minimoi-portal --format '{{.Image}}')" python -m core.memory_index.mc_index build --shelf /vault/current/shelf --release-from /vault/current --out /vault-mc/index/memory-mc.sqlite --private /vault-mc/career_filter.private.json --require-private >> /opt/minimoi/logs/vault_mc_index.log 2>&1
   ```
3. **Grant:** at an interactive terminal:
   `docker run --rm -it -v /opt/minimoi/vault-mc/memory-ask:/r <portal image> python -m core.memory_ask.agents_cli --registry /r/agents-mc.json add mc --raw`.
   Write the token to `/opt/minimoi/mc/portal/vault-ask-mc-token` (root 0600).
4. **Start the service:** add `vault-mc` to `COMPOSE_PROFILES`, set `MINIMOI_MC_VAULT_ASK_URL=http://vault-ask-mc:8772` in `/opt/minimoi/.env`, start `vault-ask-mc`, recreate the portal. Later portal releases recreate `vault-ask-mc` beside it (`deploy_scoped_release.sh`).
5. **Policy:** `deploy_mc_container_ec2.sh approve-policy "<Robert's words>"` for revision `mc-prod-20261114-web-fetch-vault-search` (one approval covers #589's web fetch rule and this one).

## CoS's held records in production (`unknown_provider`)

- **Cause** (hypothesis from the 13 Nov check; confirm in `/opt/minimoi/logs/vault_apply.log` and the index's `records` table): vault-apply's cron names a fixed `--index-runner docker:portal@sha256:bf431eab…`. That image predates the `minimoi-cos` provider. Also, an unchanged held file was never looked at again.
- **Fix in code:**
  - `--index-runner docker-container:minimoi-portal` resolves the running portal's image on each run.
  - The indexer re-checks records held as `unknown_provider` or `awaiting_reprocess`.
- **At release, once (a production write):**
  1. Change the cron line's runner to `docker-container:minimoi-portal`.
  2. Run the existing cron command once by hand with `--rebuild-index` added.
  3. Check that the index's `records` table holds no `minimoi-cos` row with `state='held'`, and that CoS finds one of her own conversations.
