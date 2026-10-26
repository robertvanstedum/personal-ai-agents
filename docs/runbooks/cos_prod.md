# Runbook — Chief of Staff with Master Craftsman's working skills (production)

**26 Oct 2026 · Claude Code.** Robert approved giving CoS the working skills MC has in prod, with CoS's own access
(#215, #335). CoS stays in its own container, `minimoi-cos-agent-a`, behind `cos-scheduler` and the `/app/cos` page.
Phase A (this runbook) gives CoS files, cited search on its own key, recall and the Vault. Phase B (the portal tools
path with a CoS policy that Robert approves by digest) follows as its own change.

## What CoS can do after phase A

| Skill | How |
|---|---|
| Take a file in the chat | 📎 in `/app/cos` → `POST /ui/upload` → `/opt/minimoi/cos/inbox/<file_id>/` (original, `extracted.txt`, `meta.json`). PDF, DOCX, text, HTML are read by the portal's sandboxed reader; pictures are kept. A message may be the attachment alone. |
| Read files and folders | OpenClaw's own read tools over its mounts (below), plus the attached text placed in the turn as marked DATA |
| Write files with downloads | CoS writes under `/minimoi/cos/outbox/`; files new or changed during the turn come back as download links (the platform's receipt is the file on disk). "Files from CoS" lists the latest 50. |
| Cited web search by default | `cos-bounded-search` on CoS's own capped key; `AGENTS.md` now requires search and a source per row for companies, agencies, rankings, prices, markets |
| Recall past conversations | each Confer turn is a line in `/opt/minimoi/cos/conversations/<YYYY-MM>.jsonl` (Robert's words, attachment names, reply, downloads; never file text), read-only to CoS |
| The Vault | `cos_prod.sh vault on` adds Robert's Confer conversation (`owner`) to `MINIMOI_VAULT_ASK_CONVERSATIONS` |
| Keep preferences | `cos_memory.md` (unchanged); lessons and delegation arrive with phase B |
| Long research turns | the page starts the turn in the background and polls (`/ui/turn/<id>`); the backend allows 600 s for such a turn, so the edge's 120 s limit no longer cuts it |

## Access (test phase: wide open, Robert's words, 26 Oct)

Read: `/minimoi/data` (rw, as before), `/minimoi/docs` (rw), `/minimoi/agent_logs` (rw), `/minimoi/scripts` (**now
read-only**: root runs these on every release), the repo clone `/minimoi/repo` (ro; MC's clone, refreshed 04:40), MC's
`work`, `workshops`, `artifacts` (ro), its inbox and conversations (ro). Write: `/minimoi/cos/outbox`,
`/minimoi/planning-studio` (a working copy seeded from the repo's `planning-studio/`; changes reach git only through a
builder, since CoS has no git credential), and the folders above marked rw.

Never mounted: `/opt/minimoi` itself, `.env*`, `mc.env`, `mc/portal`, `gateway-keys.env`, `vault-reader.env`, `auth/`,
`private/`, `backups/`, `operators/`, `~/.ssh`, the Docker socket. `tests/cos/test_cos_access_mounts.py` holds this.
`cos_prod.sh audit` checks it live, from inside the container, as CoS's own user. Narrowing per subdomain is the open
item on #215.

## CoS's own capped key

`cos_prod.sh key 50` (Robert set $50 per 30 days, 26 Oct) mints a key inside the gateway (the master key never leaves
it): CoS's four routes (`minimoi-cos-agent`, `-xai-fast`, `-anthropic`, `-web-search`), chat completions and responses
only, rpm 60. It is written to `/opt/minimoi/cos/agent-key/key` (root:1000 0640), mounted read-only at `/run/cos-key`;
`apply-config.sh` uses it at start. The script also writes `COS_AGENT_MASTER_KEY=` (empty, not a secret) into
`/opt/minimoi/.env`, so compose gives the container no master key at all. It then recreates only `cos-agent-a` (about
a minute). Raise the cap: run `key <dollars>` again (rotates; the old key is deleted).
With its own key CoS depends on the gateway's key database (Postgres), as MC's search key does.

## Release steps

1. **Main release** (this PR): new `cos-scheduler`, `cos-agent-a` and `portal` images, the compose mounts, and
   `cos_prod.sh prepare` in the deploy (creates `/opt/minimoi/cos/{inbox,outbox,conversations,planning-studio,agent-key}`).
   CoS restarts with the release (Robert is told first). CoS keeps the master key until step 2.
2. `sudo /opt/minimoi/scripts/cos_prod.sh key 50`, then `cos_prod.sh audit` (no credential readable; no master key).
3. `sudo /opt/minimoi/scripts/cos_prod.sh vault on`.
4. **Warm-up turn** in `/app/cos` ("hello"), then Robert's agency question with his resume attached: expect sources
   for each agency and a download link.

## Rollback

- Key: `cos_prod.sh key off` (master key back; CoS recreated).
- Vault: `cos_prod.sh vault off`.
- Everything: first `cos_prod.sh key off` (an older image cannot read the key file, and with
  `COS_AGENT_MASTER_KEY=` set it would have no key at all), then redeploy the previous tag for the three services:
  `sudo /opt/minimoi/scripts/deploy_scoped_release.sh <previous tag> cos-agent-a cos-scheduler portal`. The
  `/opt/minimoi/cos` folders can stay; nothing else reads them. The release snapshots CoS Agent A's volumes before
  recreating it (`/opt/minimoi/backups/cos-agent-a/`).
