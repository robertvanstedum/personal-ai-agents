# Spec — Master Craftsman in its own container (dev first, prod next), v0.2

**25 Oct 2026 · Claude Code (MC build owner) · draft for Robert, then Codex
review. Dev build authorized by Robert on 24–25 Oct; prod needs this spec
accepted plus a separate release.**

Inputs: Robert's direction on 25 Oct ("each agent gets its own container",
"dev should emulate production", "toggle to the new one and back"); OpenClaw's
review of the plan (25 Oct); MC's own review (25 Oct 09:12); Codex's handoffs;
spec v0.2 for judgment capture.

## 1. House pattern (decision recorded)

**A domain owns an agent; the agent runs in its own container.** Domain
services (the portal, Board) stay in their own containers, and the agent
reaches them only through governed tools and APIs. CoS already follows this
(`cos-agent-a`, separate from `cos-scheduler` and `cos-bot`). MC follows it
next. The shared CoS/MC role inside `cos-agent-a` (the 13 Oct experiment)
retires when prod MC cuts over.

## 2. Shape

| Part | Decision |
|---|---|
| Containers | `mc-agent` (OpenClaw runtime) plus `mc-relay` (the portal's only way in), one compose project (`docker-compose.mc.yml`), built from one image. The orphaned `minimoi-mc-relay` is rebuilt as part of this pair; there is never a second MC-shaped service |
| Persona | Laid out at start from a pinned release folder `services/mc_operations/personas/mc-chat/releases/<v>/`. **Start refuses** if any laid-out file's hash differs from the release manifest, or if the AGENTS header version differs from the folder name (the 0.1.6-label defect cannot recur) |
| Base release | **0.1.12** = 0.1.11 (core parity + capture), with the header fixed and the runtime binding rewritten for the container (tools as granted in §4) |
| Config | Written for the container; never copied from `~/.openclaw/openclaw.json`, which holds other agents and secrets |
| Model | Through the MiniMoi gateway, on MC's own capped virtual key. **No direct provider key in the container.** See §3 |
| Memory | `mem_limit` **768 MB** to start (the native gateway runs at about 137 MB, the whole native stack at about 0.33 GB), measured under load, raised only on evidence. The Docker VM has 4 GB, about 2 GB in use; freeing host memory doesn't help the VM |
| State | A fresh session database in MC's own volume. Continuity comes from MiniMoi records (spec v0.2 §6). The native database stays as a read-only archive. `apprenticeship/` is copied read-only; `task-source/` is not (it's superseded) |

## 3. Model access — **v0.2: Claude CLI route on Robert's plan** (supersedes the v0.1 text below)

Robert decided on 25 Oct that the container uses his Claude plan through
OpenClaw's documented Claude CLI route. Native OpenClaw was switched to the
same route the same day, and verified: `provider=claude-cli`, zero direct
Anthropic API calls afterwards.

- **Claude Code is pinned in the image:** a fixed version, installed at
  build, with `DISABLE_AUTOUPDATER=1`. It's never fetched at runtime (OpenClaw
  docs, "Claude CLI backend in Docker": the image doesn't ship it).
- **Sign-in state lives on a dedicated volume only:**
  `minimoi-dev-mc-claude-home` at `/home/node/.claude`, with
  `CLAUDE_CONFIG_DIR=/home/node/.claude` so `.claude.json` lives there too.
  It's never bind-mounted anywhere else, and never copied from the host.
- **Robert signs in once,** with `claude auth login` inside the container. It's
  his account, done in his own browser; no agent handles it. Then
  `openclaw models auth login --provider anthropic --method cli --set-default`.
- **Config:** the Anthropic models MC can select get
  `agentRuntime: {id: "claude-cli"}`, and `auth.order` has **no API-key
  profile**. The container holds no provider key at all.
- **Residual risk, tested in §6:** the sign-in file sits in MC's own
  container. MC's `exec` policy denies reads of `/home/node/.claude`, and the
  security test checks that it can't read it.
- **Plan limits are the ceiling.** When they're hit, MC errors loudly; there's
  no metered fallback.

## 3a. v0.1 text, kept for lineage: model credential (OpenClaw review, point 1)

Verified 25 Oct: native dev MC calls **Anthropic directly with a metered API
key** (`~/.openclaw/auth-profiles.json` holds a single `anthropic.apiKey`
profile; the per-agent auth store is empty). That bypasses the gateway, so
there's no MiniMoi cap and no receipt. Native dev MC's model is `claude-opus-5`;
prod MC runs Grok 4.3 (low).

The container calls `http://model-gateway:4000/v1` with `MC_MODEL_GATEWAY_KEY`,
MC's own capped virtual key, created in September and held in
`~/minimoi-staging/mc.env`. The dev gateway route `minimoi-mc-agent`
currently maps to `xai/grok-4.3`.

**Decision for Robert (M1):** which model the container uses.
- **(a)** Keep Opus 5. Point the `minimoi-mc-agent` route at
  `anthropic/claude-opus-5`, using MC's own Anthropic key (already in
  `mc.env`). Metered, but capped and receipted.
- **(b)** Grok 4.3, as prod does today.

Recommendation: **(a)** for the dev comparison, so the toggle tests the
container and not a model change. Changing the route recreates only the
gateway, which drops CoS calls for about 30 s; schedule that.

**Decision M2:** the monthly cap on MC's key (September default: $15).

## 4. Access (OpenClaw review, point 3; Robert's folder list)

The container is the wall. Inside it, MC gets native core parity: `read`,
`ls`, `exec` and `process`, plus MiniMoi tools, with **no** `fs.workspaceOnly`
restriction inside the container. That restriction exists to protect the host,
and the host is now outside the container.

Mounts:

| Host path | Container path | Mode |
|---|---|---|
| A dedicated MC worktree of `personal-ai-agents` (no `.env`) | `/minimoi/repo` | rw |
| `~/minimoi-private` | `/minimoi/private` | rw (Robert's decision; the judgment-store rule becomes instruction-level and is recorded as his decision) |
| `~/minimoi-work`, `~/minimoi-workshops`, `~/minimoi-inbox` | `/minimoi/<name>` | rw |
| `~/minimoi-german-state`, `~/minimoi-portuguese-state` | `/minimoi/<name>` | rw |
| `~/minimoi-staging/data` | `/minimoi/staging-data` | ro |
| `~/Downloads` | `/minimoi/downloads` | ro |
| `~/.openclaw/workspace-mc-chat/apprenticeship` (copied once) | its workspace | ro |

**Never mounted:** `~/minimoi-runtime/credentials`, `~/minimoi-staging/secrets`,
`~/.openclaw`, `~/.ssh`, any `.env`, and **the Docker socket**, which is
root-equivalent.

Host facts that today come from host-side helpers:
- **Container state** (`docker ps`) → a narrow, read-only status endpoint on
  the portal's internal tools API: names, image labels with commit
  provenance, uptime. Nothing else.
- **`minimoi_host_read` / `minimoi_host_list`** → served from the mounts
  inside the container, with modification times in listings.
- **Artifacts and career trial** → run inside the container against the
  mounted paths.

Network: `mc-net` (the gateway) plus a route to the portal's internal tools
endpoint only (`/internal/mc/v1/tools`, its own token). Web search goes
through a **bounded search plugin**, as CoS does: the query only, sent to the
gateway's search route. No open `web_fetch` until §8 option 2, a research
intermediary, exists.

## 5. Toggle and rollback (Robert; OpenClaw review, points 4 and 5)

- **Both MCs run at the same time.** Native stays exactly as it is; the
  container comes up beside it.
- **The switch** is the portal's `MC_RUNTIME_URL` and `MC_RUNTIME_CHAT_MODEL`:
  native relay (`host.lima.internal:18796`, `openclaw/mc-chat`) or container
  relay (`mc-relay`, `openclaw/mc-agent`). Each flip recreates only the
  portal, a blip of about 30 s. Before and after settings are written
  **before** the first flip.
- **Rollback = flip back.** It is rehearsed once (native → container →
  native) before Robert uses the container.
- **The capture collector** reads whichever MC is live; both databases are
  listed in its config.
- **Native stops only after Robert says the container has earned it,** never
  in the same step as the first switch.

## 6. Light security test (gates the switch)

1. It cannot read other agents' credentials or the never-mounted paths
   (`~/.openclaw`, `secrets/`, `credentials/`, `~/.ssh`).
2. It cannot reach the Docker socket or host services beyond the gateway and
   the tools endpoint.
3. It cannot write outside the rw mounts. ro mounts refuse writes.
4. Search cannot be pointed at an arbitrary URL; a query carrying a planted
   secret string is refused or redacted by the plugin.
5. A fetched or uploaded document containing instructions ("run …", "send …")
   stays data: MC reports it and doesn't act on it.
6. Start refuses on a persona hash mismatch or a header/version mismatch.
7. The capture loop works in the container: correction → block → collector.

## 7. Build steps

1. Disk: clear the build cache (1.5 GB, regenerable). Leave volumes alone
   until inspected.
2. Release 0.1.12; a start-time manifest and header check (§2).
3. Image: the existing skeleton (`start-mc.sh` check-then-serve, `selfcheck`,
   tini, `cap_drop ALL`, `no-new-privileges`) with the new config, plugins
   (minimoi-tools adapted for in-container helpers, bounded search) and the
   mounts. `selfcheck` is updated to assert this spec's tool policy and
   mounts.
4. Gateway route per M1 (scheduled ~30 s CoS blip).
5. Container up on the side; §6 tests; side-by-side questions (last night's
   questions, so continuity from records is judged before switching).
6. Rehearse flip and flip-back; then hand the toggle to Robert.

## 7a. Phases (v0.2)

- **Phase 1, now:** MC core in the container:
  - persona 0.1.12 with capture;
  - the Claude CLI runtime on the plan;
  - the mounts;
  - native core tools (`read`, `ls`, `exec`, `process`) inside the wall;
  - bounded web search through the gateway.

  The toggle test covers conversation, files, capture and continuity. Board,
  host and agent-contact tools stay on native MC until phase 2.
- **Phase 2:** the `minimoi-tools` plugin in the container, with its helpers
  running inside it against the mounts, and the portal's internal tools
  endpoint reached on a dedicated network, never the portal's front network.
- **Phase 3:** prod (§8).

## 8. Later

- Prod: the same image and compose project on EC2, with an EC2 memory
  headroom check first; cut over from `cos-agent-a`'s shared role.
- A research intermediary (Curator-style) before MC runs unattended.
- CoS's own private access follows the same pattern.
