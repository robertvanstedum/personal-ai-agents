# Plan — MC container phase 2: MiniMoi tools inside the container

**25 Oct 2026 · Claude Code (MC build owner).** Robert: "Yes, start phase 2 now: all MiniMoi tools inside the container,
dev first, then the EC2 journeys." His gate: no prod switch until MC in the container is a real partner (Board, artifacts,
recall, delegation, web search), as native MC is on dev. Spec: `spec_mc_agent_container v0.2` §4 and §7a phase 2.

## Facts (mapped from the code, 25 Oct)
- The tools are the `minimoi-tools` OpenClaw plugin (`services/mc_operations/plugins/minimoi-tools/`). Every tool POSTs to the
  portal's internal tools listener (`/internal/mc/v1/tools/<tool>`, bearer token; the portal holds only its SHA-256).
- Host tools run a fixed helper locally: `host_read`/`host_list` → `scripts/mc_host/inspect.py`; `working_write`,
  `conversation_export`, `artifact_recover` → `scripts/mc_host/artifact.py`. `host_health` measures locally.
- The plugin accepts only an `https:` or `http://127.0.0.1:<port>` endpoint; offers host tools only when the agent's file
  tools are workspace-only; the portal hard-codes the artifact root to a Mac path.
- **One runtime per environment** (spec §7.4, `core/mc_tools/binding.py`): a newer gateway instance becomes current and
  revokes the older one's turns; a retired instance can never return. Kept: it is what "one MC" means.
- CoS web search: `cos-bounded-search` plugin; the model picks only the query; fixed gateway route
  `minimoi-cos-web-search` on a capped gateway key. Measured 25 Oct: the dev container has **no** working search today.
- Delegation: `agent_contact` hands work to the Mac's agent worker, which **pulls** from the portal on the same machine.

## Decisions
| # | Decision |
|---|---|
| D1 | The plugin and both helpers are copied into the MC image, root-owned; `start.sh` admits `minimoi-tools` (and the search plugin) and nothing else new. |
| D2 | A **door** service (same image, a fixed TCP forwarder) is the container's only way to MiniMoi: `tools → portal:5011`, `search → model-gateway:4000`, nothing else. Inside `mc-agent` a loopback forwarder serves `127.0.0.1:5011` and `:4000` → the door, so the plugin's endpoint rule holds unchanged and both stay off the egress proxy (`NO_PROXY` already covers loopback). MC's own `exec` cannot reach any other portal port. |
| D3 | `mc-chat` runs with workspace-only file tools, as native does; mounts are read through `minimoi_host_read`/`host_list`, which apply the reviewed denylist. |
| D4 | The portal's artifact root becomes configurable (`MINIMOI_MC_ARTIFACT_ROOT`, default unchanged). Dev mounts the same Mac folder at the same path; prod uses `/opt/minimoi/mc/artifacts`. |
| D5 | **Own credentials per runtime.** The container gets its own tools token; the listener accepts a short list of hashes (native's and the container's), still one runtime current at a time (D7). The search key is MC's own capped gateway key, never a provider key. |
| D6 | `host_health`: disk paths of the container's mounts; no Docker CLI or socket (spec §4). Container state comes later from a narrow portal status endpoint. |
| D7 | The dev toggle hands the tools over with the chat; toggling back restarts native's gateway so it registers fresh. Prod only ever has the container. |
| D8 | Search: an `mc-bounded-search` plugin, a copy of CoS's with MC's own route and key. Delegation from prod (Mac worker polling minimoi.ai over HTTPS) is phase 2c, designed separately. |

## Slices
- **2a — the 25 tools in the dev container** (D1–D4, D6, D7): Board read/write/undo, recall, describe, GitHub issues, guest
  requests, preferences/lessons, host read/list/health, artifacts, agent contact/status/watch (to the Mac worker, dev only).
- **2b — web search** (D5 search key, D8).
- **2c — delegation from prod** (Mac worker pulls from prod; no inbound to the Mac).
- **Prod**: the same in `docker-compose.mc.prod.yml`, then the EC2 journeys: update a Board note, read an artifact, recall a
  past conversation and the Vault, search the web with sources, delegate and report back, open a Room on request.

## Journeys (the acceptance, dev first)
Each on the container, compared with native MC answering the same request: Board note create→update→undo; read a named
artifact; recall last night's conversation; GitHub issue lookup; host health; `working_write` an artifact and read it
back; agent contact to Claude Code with the result reported back; web search with cited sources; meeting proposal.
