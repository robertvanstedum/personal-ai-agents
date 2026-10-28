# Runbook — Master Craftsman's own container in production

**25 Oct 2026 · Claude Code (MC build owner).** Implements `docs/specs/spec_mc_agent_container_v0.1_2026-10-25.md`
(v0.2) phase 3. Today prod MC is the shared CoS/MC role inside `cos-agent-a` (portal → `cos-scheduler` →
`cos-agent-a`, selected by `MINIMOI_GUILD_CHAT=cos`). This moves the Guild chat to MC's own container and back.

## What ships where

| Piece | File | Deployed by |
|---|---|---|
| Prod gate: `MINIMOI_GUILD_CHAT=mc` admits the OpenClaw backend only toward `http://mc-relay:8790/v1` with a caller token | `minimoi_portal/guild_mounts.py` | ordinary main release (portal) |
| `minimoi-mc-portal` network, joined by the portal only (internal, no route out) | `docker-compose.prod.yml` | ordinary main release |
| MC project: `mc-egress`, `mc-agent`, `mc-relay` (compose project `minimoi-mc`) | `docker-compose.mc.prod.yml` | tag `mc-agent-v*` → `.github/workflows/deploy-mc-agent.yml` |
| Deploy / status / switch / collect | `scripts/operations/deploy_mc_container_ec2.sh` | same workflow, to `/opt/minimoi/scripts/` |
| Judgment collector | `scripts/judgment/judgment_store.py` | same workflow |
| Security and model checks (`--target prod`) | `scripts/ops/mc_container_security_test.py`, `mc_container_model_tests.py` | same workflow |

The image is `minimoi/cos-scheduler:mc-agent-v…` in ECR (the existing repository, as for `agent-a-*` and
`model-gateway-*`), built from `docker/mc-agent-container/Dockerfile` with persona **0.1.12** (the release whose runtime
binding is written for the container).

## Boundaries (same as dev, checked by the security test)

- `mc-agent` sits only on `minimoi-mc-internal` (no route out). Its one way out is `mc-egress`, which tunnels only to
  model-provider hosts. No Docker socket, no `.env`, no host credentials, no provider API key (start refuses one).
- The portal reaches MC only through `mc-relay` on `minimoi-mc-portal`. MC cannot reach postgres, the other services or
  the EC2 instance metadata service (169.254.169.254, which hands out the host's AWS credentials).
- Mounts: `/opt/minimoi/mc/{work,inbox,workshops}` (rw), `/opt/minimoi/mc/apprenticeship` (ro), `/opt/minimoi/docs` (ro).
- The judgment store `/opt/minimoi/private/judgment` (root, 0700) is never mounted. The collector copies MC's database
  out of the `minimoi-mc-state` volume and reads the copy (opening the live file from the host would create root-owned
  side files in MC's volume). It runs nightly at 04:50 Chicago (`/etc/cron.d/minimoi-mc-judgment`), inside the batch window.
- Tokens are generated on the host into `/opt/minimoi/mc/mc.env` (root, 0600) and never printed. `switch mc` copies the
  relay caller token into `/opt/minimoi/.env` as `MC_RUNTIME_TOKEN`. (Moving both into Parameter Store is a follow-up.)

## Steps

1. **Main release** with the gate and the network. No behavior change: `MINIMOI_GUILD_CHAT` stays `cos`. Check the
   portal log line `chat partner 'cos'` and that `docker network inspect minimoi-mc-portal` lists only the portal.
2. **Tag `mc-agent-v0.1.12-1`** on the merged commit. The workflow builds, checks that the image refuses a provider
   key, pushes, and runs `deploy` then `status` on EC2. The relay must answer 200; the portal is not touched.
3. **Robert signs in** (about 5 minutes; nobody else handles these). AWS console → Systems Manager → Session Manager →
   start a session on `i-0d13db821169627e2`, then:
   ```
   sudo docker exec -it minimoi-mc-agent claude auth login
   sudo docker exec -it minimoi-mc-agent node /app/openclaw.mjs models auth login --agent mc-chat --provider anthropic --method cli --set-default
   sudo docker exec -it minimoi-mc-agent node /app/openclaw.mjs models auth login --agent mc-chat --provider openai --device-code   # optional, ChatGPT
   ```
   Each prints a link; finish it in your own browser. Grok waits for the SNI-filtering egress (it ignores the proxy).
4. **Checks on EC2** (Claude Code, via SSM):
   `python3 /opt/minimoi/scripts/mc_container_security_test.py --target prod` (all runnable checks pass) and
   `python3 /opt/minimoi/scripts/mc_container_model_tests.py --target prod` (plan route; refuses its sign-in file; no
   arbitrary websites; injected instructions stay data; capture loop).
5. **Gate (Robert, 25 Oct): no switch before phase 2.** Without the MiniMoi tools MC "is not a real partner": it must
   update the Board, read artifacts, recall past conversations and the Vault, and delegate and follow up, as native MC
   does on dev. Phase 1 alone would also lose the Vault context the CoS route adds today. Switch only after MC on EC2
   passes those journeys.
6. **Switch** on Robert's go: `sudo /opt/minimoi/scripts/deploy_mc_container_ec2.sh switch mc`. This rewrites four
   keys in `/opt/minimoi/.env` (backup kept beside it) and re-runs the ordinary scoped deploy for the portal alone at its
   current tag (about 30 s). Then one real Guild chat turn.
7. **Rollback** at any time: `sudo /opt/minimoi/scripts/deploy_mc_container_ec2.sh switch cos`. CoS Agent A is never
   stopped by any of this; its shared MC role retires only after Robert says the container has earned it.

## Known gaps (phase 1)

- Chat, files in its own folders, and capture only. Board, host and agent-contact tools stay with the CoS route until
  phase 2 (MiniMoi tools inside the container).
- `mc-relay`'s job audit path still names the `mc-agent` agent's database; jobs are off in phase 1.
- Grok sign-in needs the SNI-filtering egress (tomorrow).

## MC's GitHub issue writes (26 Oct 2026)

**What an agent can do on Robert's yes** (issues only, this repository only; each tool has its own policy rule, all
tier 3 with exact approval; the agent shows the exact text, Robert says yes, the portal writes with his issues-only token
and signs it with the agent's name, for example "Opened by MiniMoi's Master Craftsman after Robert approved this exact
text"):

| Tool | Capability | Switch |
|---|---|---|
| `minimoi_issue_create`: a new issue (title, body, labels) | `issue.create` | `MINIMOI_MC_TOOLS_TICKETS` |
| `minimoi_issue_update`: new title or body, the complete label set, close or reopen | `issue.update` | `MINIMOI_MC_TOOLS_COMMENTS` |
| `minimoi_issue_comment`: a comment | `issue.comment` | `MINIMOI_MC_TOOLS_COMMENTS` |
| `minimoi_data_repair_ticket`: a draft change record for guest requests | `issue.create_data_repair` | `MINIMOI_MC_TOOLS_TICKETS` |

The same tools serve CoS through its own policy file (#215). Public-text guard: plain ASCII, no email address, no
guest's name. Create and update need migration `core/mc_tools/sql/020_issue_create_update.sql`
(`mc_prod_db_migrate.sh apply`, which backs up first) and policy revision `mc-prod-20261026-github-issues`, approved
by Robert with `approve-policy`.

MC can comment on issues (`issue.comment`) and file tickets (`issue.create_data_repair`). Both are tier 3, exact
approval: MC proposes, Robert says yes in the chat, the portal writes. Both rules are already in
`mc-policy.prod.json`, so turning them on does not change the policy digest.

- **Token (Robert makes it):** GitHub → Settings → Developer settings → Fine-grained tokens. Resource owner
  `robertvanstedum`, only the `personal-ai-agents` repository, Repository permissions **Issues: Read and write**
  (Metadata read-only is added by GitHub), nothing else, 90-day expiry. Robert stores it in Parameter Store as a
  SecureString: `/minimoi/production/mc_github_issues_token`.
- **Turn on:** `sudo /opt/minimoi/scripts/deploy_mc_container_ec2.sh issues on`. It reads the parameter on the host,
  refuses anything but a fine-grained token (`github_pat_…`), writes it to `/opt/minimoi/mc/portal/github-issues-token`
  (root 0600; the portal sees it read-only at `/run/secrets/mc/github-issues-token`), sets
  `MINIMOI_MC_TOOLS_TICKETS=1` and `MINIMOI_MC_TOOLS_COMMENTS=1` in `/opt/minimoi/.env`, and redeploys the portal alone
  (about 30 s; MC's container is not restarted). It ends by printing `issue comments on | tickets on`.
- The token is never in `.env` (every main service loads `.env`) and never in MC's container.
- MC's container must also offer both tools (`docker/mc-agent-container/openclaw.json`, plugin `tools`). The ticket tool
  was left out on 25 Oct and added in `mc-agent-v0.1.14-2`; without it MC reports `plugin: not_offered`.
- **Rotate** (before the expiry): update the parameter, run `issues on` again.
- **Off:** `issues off` (removes the file and both flags). `switch cos` also clears both flags.

## MC hands Robert's requests to the agents (27 Oct 2026)

Robert tells MC on production "ask Claude Code / Codex / Grok / all to …". MC calls `minimoi_agent_ask` (`to`: the
agents his words name, or `["all"]`; a display `title`). The portal first runs the ASK line's owner-intent check on his
kept note (the agents must be exactly the ones he asks; negated, quoted or absent asks are refused, nothing recorded),
then writes **Robert's own kept message**, never MC's words, into the production request ledger
(`<guild store>/agent_requests`, origin `owner_message_via_mc`), one request per agent, ids fixed by his note. Tier 2,
standing approval, one per turn (`agent.ask.owner_note`, OPS-2026-10-27-mc-front-door-prod).

- **Turn on:** `MINIMOI_GUILD_AGENT_REQUESTS=1` in `/opt/minimoi/.env`, policy revision `mc-prod-20261027-agent-ask`
  approved by Robert with `approve-policy`, portal redeploy, and MC's container offering the tool. No migration.
- **Relay:** the supervision sync (`scripts/mc_host/supervision_sync.py`, worker token, outbound HTTPS only) pulls
  each request, creates it in the Mac's shared ledger with the same id and content, and acks it; it carries each
  agent's receipt, progress, result, needs-you and cancel back up, applied with the ledger's own rules (only the
  recipient answers; duplicates have one effect; nothing for a request that was not relayed), plus which agents are
  listening. Robert re-runs `scripts/mc_host/install_supervision_sync.sh` on the Mac to install the new sync.
- The worker token can relay; it can never create a request on production, answer a decision or act as Robert.

## MC's web search: the gateway's key database (26 Oct 2026)

MC searches through its own capped gateway key (`minimoi-cos-web-search` only, `/v1/responses` only, $5 per 30 days,
rpm 10). LiteLLM can only issue such keys with a key database. Production's gateway now has one, as staging has since
stage C (`scripts/staging/README.md`, "Master Craftsman stage C").

- **Database:** role and database `litellm_keys` in `postgres-ai-agents` (`NOSUPERUSER NOCREATEDB NOCREATEROLE`, owns
  only its own database; `CONNECT` revoked from `PUBLIC`). LiteLLM's Prisma migrations create its tables at start.
- **Password:** Robert generates it (`openssl rand -hex 24`) and stores it as the SecureString
  `/minimoi/production/litellm_keys_db_password`. `sync_cos_runtime_secrets_from_ssm.sh` (run by every main release)
  writes `/opt/minimoi/gateway-keys.env` (root 0600) with only the gateway's `DATABASE_URL`. `docker-compose.prod.yml`
  loads it into the model gateway alone (`required: false`, so a host without it runs on the master key, as before).
- **Master key:** unchanged (`/minimoi/production/model_gateway_key`). CoS keeps using it; it never needs the database.
- **MC's key:** `deploy_mc_container_ec2.sh deploy <tag>` mints it inside the gateway (the master key never leaves it)
  into `/opt/minimoi/mc/mc.env` as `MC_SEARCH_KEY`, if it is not there yet; `mc-agent` is recreated with it
  (a restart: run a warm-up turn). The key is never CoS's and never the master key.
- **Checks:** from inside `minimoi-mc-agent` through the tools door, the key answers a web search (200 with citations)
  and is refused (403) on every other model and route: chat completions, `/v1/models`, `/key/*`, the `/anthropic`
  and `/openai` pass-throughs. Then one CoS question with web search still answers with citations.
- **What changes for CoS:** with the database on, the gateway does not start while Postgres is down (it retries until
  Postgres is back), so Postgres is now a dependency of CoS's gateway too. Start Postgres first after any outage.
- **Rollback:** `sudo mv /opt/minimoi/gateway-keys.env /root/` then
  `sudo /opt/minimoi/scripts/deploy_scoped_release.sh <running tag> model-gateway`: the gateway is back on the master key
  alone (CoS unaffected; MC's key stops working and MC says search is off). The database and its role stay; putting
  the file back turns it on again. The next main release writes the file again while the parameter exists, so for
  a lasting rollback Robert also deletes `/minimoi/production/litellm_keys_db_password`.
