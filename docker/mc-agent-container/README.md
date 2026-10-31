# Master Craftsman's own container (dev, phase 1)

Spec: `docs/specs/spec_mc_agent_container_v0.2_2026-10-25.md` (registered in PR #375, tracked in issue #376).
It runs **alongside** native MC. The portal still talks to native MC until the toggle (below).

## What's running (25 Oct 2026)

| Container | Role |
|---|---|
| `minimoi-dev-mc-agent` | OpenClaw 2026.9.6 + Claude Code 2.1.308 (pinned, no self-update); persona 0.1.23 with MC handbook 0.1.1 loaded at start (`start.sh` step 2b, `handbook.mjs`); Opus 5 on the **claude-cli** route (Robert's plan); 1 GB memory cap |
| `minimoi-dev-mc-egress` | MC's only way out: a CONNECT proxy to model-provider hosts only (Anthropic, xAI, OpenAI) |

- **Network:** `minimoi-dev-mc-internal` (internal, no route out) plus the proxy.
- **Mounts:** `/minimoi/repo` (MC's own worktree at `~/minimoi-mc-repo`, branch `mc/workspace`, no `.env`), `private`, `work`, `workshops`, `inbox`, `german-state`, `portuguese-state` (rw); `downloads`, `apprenticeship` (ro).
- **Not mounted:** `~/minimoi-staging/data`, which holds other agents' tokens; `~/.openclaw`; `secrets/`; `~/.ssh`; the Docker socket.

## Robert: sign in once (each opens a link; you finish it in your own browser)

Run in Terminal:

1. **Claude** (your Max plan, through Claude Code inside the container). Open the printed link, approve, and paste the code back:

   ```bash
   docker exec -it minimoi-dev-mc-agent claude auth login
   ```

2. **Grok** (SuperGrok / X Premium), a device-code sign-in:

   ```bash
   docker exec -it minimoi-dev-mc-agent node /app/openclaw.mjs models auth login --provider xai --method oauth
   ```

3. **ChatGPT** (Codex OAuth), a device-code sign-in:

   ```bash
   docker exec -it minimoi-dev-mc-agent node /app/openclaw.mjs models auth login --provider openai --device-code
   ```

Sign-in state lives only on the `minimoi-dev-mc-claude-home` and `minimoi-dev-mc-state` volumes. No API key is used, and start-up refuses one.

## After sign-in: Claude Code finishes phase 1

- The pending security checks (`scripts/ops/mc_container_security_test.py`): search abuse, injected instructions staying data, MC unable to read its sign-in files, and the capture loop.
- A side-by-side conversation: the questions from the 24 Oct session, so continuity from records is judged before any switch.
- **The toggle.** The portal reaches MC through the native relay (relay v2: streaming, turn ledger, handoff). The container needs its own relay instance on a dedicated network before the portal can point at it. Next build step.

## Status, 25 Oct ~12:00 Chicago

- **Sign-ins:** Claude Max ✓ (both agents, through Claude Code); ChatGPT ✓ on `mc-agent`.
- **Grok: out of container MC, by Robert's decision (25 Oct).**
  - **Why:** xAI's OAuth completes by calling `cli-chat-proxy.grok.com` with a client that ignores the proxy settings. It tried to resolve the host directly and failed (`EAI_AGAIN`), and the proxy log never saw the request. That host is probably also Grok's subscription chat path.
  - **Fix, later (phase 2):** an SNI-filtering exit that enforces the allowlist even for clients that ignore the proxy. Direct egress is not opened.
  - **Meanwhile:** Grok stays available on the Mac through personal OpenClaw. Web search (provider `grok`) is unavailable in the container until then.
- **Multi-agent sign-ins:** commands now need `--agent mc-chat` (conversations) or `--agent mc-agent` (jobs).
- **Security:** 11/11 runnable checks pass (`SECURITY_TEST_2.json`). 5/5 model checks pass (`MODEL_TEST_3.json`): plan route; refuses to reveal its sign-in file; no arbitrary websites (the proxy blocked both WebFetch and curl); an injected instruction in a file was reported as an attack, not followed; the capture loop works.
- **Relay:** `minimoi-dev-mc-relay` on `127.0.0.1:18797`, the same caller token and model names as native. `scripts/ops/mc_relay_check.py` passes.
- **Toggle:** `scripts/ops/mc_toggle.py to container` and `to native`. Rehearsed both directions on 25 Oct with all checks OK. **Dev default: native**, until phase 2 brings the MiniMoi tools (Board, host, agent contact) into the container.
- **Memory:** cap 2 GiB; peak about 1.46 GiB during turns. Running it alongside the full dev stack needs the Docker VM's headroom watched (it has 3.8 GiB).
- **Lessons:**
  - Never write `openclaw.json` directly: OpenClaw restores its own copy (`*.clobbered.*`). Policy is applied with `openclaw config patch`.
  - A hard stop leaves the gateway lease held about 4 minutes. Hence the 120 s stop grace and the bounded retries.
  - Under the Claude CLI runtime, Claude Code's built-in tools aren't bound by OpenClaw's tool policy. The network wall is the boundary.

## Commands

- Status / logs: `docker logs minimoi-dev-mc-agent`, `docker logs minimoi-dev-mc-egress`
- Security test: `python3 -I scripts/ops/mc_container_security_test.py --receipt ~/minimoi-runtime/mc-container/SECURITY_TEST_n.json`
- Stop (volumes kept): `docker compose -p minimoi-dev-mc --env-file ~/minimoi-runtime/mc-container/mc.env -f docker-compose.mc-agent.dev.yml down`
- **Rollback:** nothing to roll back while the portal isn't pointed here. Native MC is untouched.
