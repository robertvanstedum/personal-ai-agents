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
