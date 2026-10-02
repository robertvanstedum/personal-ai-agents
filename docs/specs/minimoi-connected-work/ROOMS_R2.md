# Rooms R2 — Claude Code and Codex join a room · specification v0.4 (Codex amendment, review candidate)

**Edition:** v0.4 · 2 October 2026 (v0.3: 1 October) · **Author:** Claude Code (sole implementation editor) · **Reviewer:** Codex (independent) · **Decision owner:** Robert
**Status:** v0.4 unparks Codex (§3.6) on Robert's direction of 2 October 2026 ("Codex and Claude are the same … codex setup now, grok to follow later"; "finish the room test with 3 of us"); everything else is v0.3 unchanged. Codex cleared v0.2 for the Claude-only implementation (`CODEX_RECHECK_R2_R3_v0.2_2026-10-01.md`); v0.3 folds in its four acceptance clarifications and two adjustments found while building (§0). v0.2 answered Codex's review (`CODEX_REVIEW_R1_PREFLIGHT_R2_R3_2026-10-01.md`, R2-01…R2-06). Robert authorized R2 on dev on 1 October 2026; production, main merges and paid/live proofs keep their gates.
**Builds on:** [R1](ROOMS_R1.md) v0.5.1, deployed on dev at release `d33a400` (branch base `d33a4002`). `records/` means `prototype-lab/projects/project-records-room-poc/`.

## 0. Changes from v0.1 (finding map)

| Finding | Resolution in v0.2 |
|---|---|
| R2-01 isolation | Claude Code: no tools at all plus a startup-input allowlist checked before every turn (§3.3). **Codex is parked** (fail-closed, §3.6): its effective tool list is not inspectable without a model call, its default input carries local skill and multi-agent instructions, and its shell could read its own sign-in file. |
| R2-02 legacy bypass | The fence applies by **teammate identity**: any post by a principal with a teammate card needs a running claim, whatever the credential or origin (§3.4). `claude-code`'s legacy credential is revoked at provisioning; manual posting moves to a distinct principal. |
| R2-03 adapter and multi-teammate | Runtime identity per teammate in the adapter; one worker per hosted teammate with its own credential; recovery filtered by principal (also fixed for the MC worker); housekeeping on its own thread so reach stays truthful during a long turn (§3.2). |
| R2-04 loopback door | Docker does not publish ports for a container that sits only on internal networks, so Records itself is **not** given a port. A small forwarding sidecar, `records-door`, publishes `127.0.0.1:18881` and forwards only to `minimoi-records:18880`; Records' attachments and egress are unchanged. Named amendments to R1 §1/§3.8, compose, preflight, tests and docs (§3.1). |
| R2-05 process outcome | After a CLI process starts, the outcome is `uncertain` unless a trusted pre-inference rejection is proven; races and caps defined (§3.5). |
| Recheck: startup loaders | §3.3 adds the managed location `/Library/Application Support/ClaudeCode` and `.claude/CLAUDE.md` / `.claude/rules` in every ancestor; paths resolved; anything unreadable fails closed; memory auto-loading verified absent (no project folder for the fresh working directory), not inferred from an empty tool list. Robert's normal configuration is only read, never changed. |
| Recheck: init and evidence | §3.3/§3.7: a valid empty-tools/MCP `system/init` must be the first event; missing, malformed or contradictory init fails closed; proof evidence is written only after Records accepted the proof reply, and bound to the executed binary and profile |
| Recheck: door and concurrency | §3.1: door tested from the Mac (401 without credential, 200 with the connector's) and on the LAN address (connection refused); Records' attachments asserted unchanged; housekeeping skips any turn being delivered (in-flight set) on top of R1 idempotency |
| Recheck: Codex visible | §3.6 and every handoff state Codex in Rooms as **incomplete**; no tool-enabled or API fallback |
| Built: fence by identity only | §3.4: a post is a teammate post when its principal has a card or names a turn. R1's extra rule "any membership-scoped credential" is dropped: every teammate holds a card, so identity covers each teammate's credential, and a cardless `-manual` principal posts as itself. |
| Built: facilitator | §3.2: inviting MC makes it the facilitator whatever the invitation order (owner decision R1 §0); otherwise the first teammate invited keeps the role |
| R2-06 proof lifecycle | No-inference checks named; the init-event check happens inside the one owner-approved Prove; evidence bound to binary hash, version and flag profile; a change makes the teammate *away* until proven again (§3.7). |

### v0.4 — Codex amendment

| Item | Resolution |
|---|---|
| R2-01 Codex parked | Unparked with evidence (`_working/rooms-redesign/codex-boundary-evidence-2026-10-02/EVIDENCE.md`, E1–E11, no model call): a Rooms flag set and a text-only model catalog reduce what Codex 0.145.0 offers the model to `update_plan`, `request_user_input`, `view_image`, and `view_image` is refused before any file read; local skills, rules and instructions are absent from the model-visible input. Codex runs in its own container through `codex app-server`, with its own subscription sign-in (§3.6). |
| Remaining gap (E11) | The tool list under a real ChatGPT sign-in cannot be captured offline (requests go over a fixed `wss://chatgpt.com` endpoint). Covered at runtime: any non-message item or any server request fails the turn closed and sends Codex *away* (§3.6.4); and the container holds nothing of Robert's for a tool to reach (§3.6.2). |
| One teammate experience | Same Invite, Prove, answer, Stop, *away* and visible failures as Claude Code; same worker, journal, fence, proof lifecycle; the runner is the only provider-specific part. Grok is the next runner (not this build). |

## 1. Outcome

In a Rooms meeting Robert invites **Claude Code** from the Invite dialog. Addressed with `@Claude` (or its recipient chip), Claude Code answers one bounded, tool-free discussion turn, attributed to `claude-code`, through Robert's existing Claude subscription on this Mac. No API key, no tool, no file access from the model. When the connector stops or its runner changes, Claude Code shows *away* with the real reason within three minutes; when the Mac sleeps the whole dev site is down (R1 §3.9 case a).

**v0.4:** Codex joins the same way: invited from Invite, addressed with `@Codex`, one bounded tool-free turn attributed to `codex`, through Robert's ChatGPT subscription signed in once inside its own container. With Claude Code and MC this makes the three-teammate meeting.

R2 proves: one in-room turn from Claude Code and one from Codex, stop discards late output, connector loss shows *away*. It does not prove: Grok, tools or repository work from a meeting, laptop-off participation, CoS, rounds, voice, production.

## 2. Verified facts (1 October 2026, read-only, no model call)

| Fact | Evidence |
|---|---|
| Claude Code 2.1.76 at `/opt/homebrew/bin/claude`; `claude auth status`: signed in through claude.ai, Pro; no `ANTHROPIC_API_KEY` in the environment | CLI output |
| With an empty dedicated `HOME`, `claude auth status` reports **signed out**: the subscription sign-in is tied to Robert's real home, so the runner keeps `HOME` and isolates by other means | CLI output |
| Robert's user-level Claude configuration holds no instruction file (`~/.claude/CLAUDE.md` and `~/.claude/rules` absent) and `settings.json` holds only display preferences | file listing |
| Headless flags present: `-p`, `--output-format json|stream-json`, `--tools ""`, `--strict-mcp-config`, `--mcp-config`, `--setting-sources`, `--system-prompt`, `--no-session-persistence`, `--disable-slash-commands` | `claude --help` |
| Codex (ChatGPT-app bundled 0.158.0-alpha.2): `codex debug prompt-input` renders the model-visible input without a model call; it includes local skills instructions and a multi-agent role, and **no tool list**; feature flags (`shell_tool`, `unified_exec`, `plugins`, `apps`, `browser_use`, `computer_use`, …) exist but their effective result is not shown | CLI output |
| **v0.4** Codex CLI 0.145.0 (standalone, SHA-256 `1da3f4e0…705f590`); npm publishes the same version for `linux-arm64` (`@openai/codex@0.145.0-linux-arm64`) | `npm view`; binary hash |
| **v0.4** Effective tool listing and model-visible input, captured from the request Codex sends to a loopback fake endpoint, defaults vs Rooms flag set; `view_image` behaviour; `exec --json` hides tool calls while app-server has `imageView`/`commandExecution`/… items; `chatgptAuthTokens` login is "internal use only"; `codex login --device-auth` exists | EVIDENCE.md E1–E11; `codex app-server generate-json-schema` |
| Docker publishes no host port for a container attached only to `internal: true` networks; `minimoi-records` is on `minimoi-staging-records` only | Docker networking rule; R1 compose |

## 3. Design

### 3.1 The door: a forwarding sidecar (amends R1 §1 and §3.8)

- New service `records-door` in `docker-compose.records.yml`: the rooms-worker base image (python only, no secrets, no env file), a ~40-line TCP forwarder (`services/records_door/door.py`) that accepts on `0.0.0.0:18881` inside the container and connects **only** to `minimoi-records:18880`. It is attached to `records-net` and to a new non-internal bridge `minimoi-staging-records-door`; its port is published as `127.0.0.1:18881:18881` only. `read_only`, `cap_drop: ALL`, `no-new-privileges`, `mem_limit 64m`.
- `RECORDS_ALLOWED_HOSTS` adds `127.0.0.1:18881` (the Host header the Mac connector sends). Records itself gains **no** port, no network, no egress.
- **R1 amendments named:** §1 "networks" list gains `minimoi-staging-records-door` (door only); §3.8 notes the door; `records.sh preflight` checks: Records published ports `{}` (unchanged), door published ports exactly `127.0.0.1:18881`, door attachments exactly `records-net` + `records-door`, door environment holds no credential; from the Mac an authenticated `GET /api/v1/hosted-teammates` with the connector's credential answers 200 and an unauthenticated one 401; a connection to the Mac's LAN address on 18881 is refused. Tests and the staging README are amended. **Rollback:** remove the door service and network; R1 is unchanged.

### 3.2 The connector process

- `services/rooms_connector/` runs on the Mac (launchd agent `com.vanstedum.minimoi-rooms-connector`, KeepAlive) from a private virtualenv `~/minimoi-staging/connector-venv` (`requests` only), with `RECORDS_URL=http://127.0.0.1:18881`.
- **One worker per hosted teammate.** The R1 `Worker` class is reused with: its own teammate principal, its own membership credential, its own runner, and an adapter configured with that teammate's runtime identity (`agent_id`, `runtime`). `adapter.reply_payload` takes `agent_id` and `runtime` as parameters (MC keeps `mc-agent`/`OpenClaw`); Claude Code uses `agent_id='claude-code'`, `runtime='Claude Code CLI 2.1.76'`.
- **Recovery filtered by principal:** `housekeeping` handles only invites and uncertain turns whose `principal` is the worker's own teammate. (This also fixes a latent R1 gap: the MC worker trusted the installation's binding to host only MC.)
- **Housekeeping on its own thread** every 10 s (invites, reconciliation, readiness), independent of a turn in progress, so a 180-second turn does not stale the reach of the connector's other teammates. Turn handling stays sequential. An in-flight set guarantees housekeeping never reconciles or delivers a turn the main loop is delivering.
- **Facilitator.** Inviting MC makes it the meeting's facilitator whatever the invitation order (owner decision, R1 §0); a meeting without MC keeps its first invited teammate as facilitator.
- Separate work-scoped service principal `rooms-connector-mac`, bound only to `claude-code` in R2. The MC worker's binding is unchanged; neither worker can claim the other's teammates.

### 3.3 The Claude Code runner and its boundary (R2-01)

Command, run in a fresh empty temporary directory (mode 700) under `/private/tmp`, with a stripped environment (`HOME`, `PATH=/usr/bin:/bin:/opt/homebrew/bin`, `LANG`, `USER` only; no `ANTHROPIC_*`):

```
/opt/homebrew/bin/claude -p --output-format stream-json --verbose --tools "" \
  --strict-mcp-config --mcp-config '{"mcpServers":{}}' --setting-sources "" \
  --disable-slash-commands --no-session-persistence --system-prompt "<etiquette + brief>"
# stdin: the attributed transcript JSON, then the trigger (R1 §3.10 boundary)
```

- **Tool boundary (enforced by the CLI, observed per turn):** the stream's first event must be `system/init` with `tools` empty, `mcp_servers` empty and `plugins` (if present) empty; a missing, malformed or contradictory first event fails closed. No answer is accepted before it, and receiving a result is never taken as proof. Anything else kills the process and fails the turn `uncertain` (a request may already be in flight) with reason `runner_boundary`; the teammate goes *away* until proven again. With no tools, the model cannot read files, including sign-in files, whatever the process itself can read.
- **Startup-input allowlist (checked before every turn, no model call):** the runner refuses to start (definitive `failed`, reason `startup_inputs`, no process started) if any of these exist: `~/.claude/CLAUDE.md`, `~/.claude/rules/`, the managed location `/Library/Application Support/ClaudeCode`, a `CLAUDE.md`, `CLAUDE.local.md`, `.claude/CLAUDE.md` or `.claude/rules` in the (resolved) working directory or any ancestor, a project folder for the working directory under `~/.claude/projects/` (or any project folder naming it), or a user `settings.json` key other than the display preferences found today (`theme`, `agentPushNotifEnabled`, `inputNeededNotifEnabled`). A path that cannot be read fails closed. `--setting-sources ""` excludes user, project and local settings, so hooks and plugins configured there do not load; the allowlist guards the files that settings do not cover.
- **No silent API fallback:** `ready()` requires `claude auth status` to report `loggedIn: true` with `authMethod: claude.ai`; the stripped environment holds no API key.

### 3.4 The fence by identity (R2-02)

`fence_append` treats a post as a teammate post when the authenticated principal has a teammate card or the post names a turn, regardless of credential scope or origin. (R1's additional "any membership-scoped credential" rule is dropped: every teammate holds a card, so identity already covers each teammate's credential.) Such a post needs a running claim (R1 §3.5) or it is refused 409 with no row. Provisioning revokes `claude-code`'s existing legacy credential (listed in the provisioning output) and creates a separate principal `claude-code-manual` ("Claude Code (manual)") for any future hand-posted note, with its own credential, no card, and no ability to post as `claude-code`. Tests: a legacy `claude-code` token is refused; a card principal's no-origin post to a paused meeting is refused; a stale-generation post by identity is refused; `claude-code-manual` posts as itself. Codex has no card in R2, so its manual path is unchanged.

### 3.5 Process outcomes (R2-05)

| Situation | Outcome |
|---|---|
| Checks fail before any process starts (startup inputs, auth status, runner changed) | `failed`, definitive, no process |
| Process started, then exits non-zero with no output | `uncertain` (a request may have been consumed) |
| Process started, `system/init` violates the boundary | killed; `uncertain`, reason `runner_boundary` |
| Process finishes with a result event, `is_error` false, non-empty text | answer delivered (journaled first) |
| Result event `is_error` true | `uncertain` unless its subtype is a documented pre-request refusal (none relied on in R2) |
| All preparation done (fingerprint, sign-in, startup inputs, working directory) | the runner asks Records for a **fresh dispatch admission at the spawn boundary**, inside the turn's absolute deadline; refused or stale → no process (`not_admitted`); nothing that can block runs between that answer and the spawn |
| Process exits non-zero after a clean result, a later `init` or tool declaration appears, an event is not a JSON object, or a second result arrives | `uncertain` (the lifecycle must be clean end to end) |
| Stop requested before the process is registered | the runner checks the stop flag after spawn and kills at once; if the flag is set before spawn, no process starts and the turn is acknowledged stopped |
| `max_turn_s` (180 s) exceeded | process group killed (TERM, then KILL after 5 s); `uncertain` |

Captured output is capped at 1 MB, enforced on bounded chunk reads before anything is buffered or parsed; beyond it the process is killed and the turn is `uncertain`. The child is always terminated and reaped, and its registration cleared, on every exit path. Child processes run in their own process group and are killed with it. No restart or Retry runs inference automatically; R1's reconciliation gate applies.

### 3.6 Codex: a container connector (v0.4; replaces "parked")

#### 3.6.1 Process model

- New service **`rooms-codex`** in `docker-compose.records.yml`, image `minimoi-staging/rooms-codex:<tag>` built by `records.sh build` from `docker/Dockerfile.rooms-codex`: `python:3.12-slim`, `requests`, the repository's `services/rooms_worker` + `services/rooms_connector`, the Codex **0.145.0 linux-arm64** binary from the npm tarball (version and SHA-256 pinned in the Dockerfile; the build fails on a mismatch), and the Rooms model catalog `services/rooms_connector/codex_catalog.json` (read-only in the image).
- One long-running Python connector process (`python -m services.rooms_connector.connector`, `ROOMS_TEAMMATE=codex`) runs the R1 worker with a `CodexRunner`. It reaches Records directly at `http://minimoi-records:18880` (no door). **One `codex app-server` child per turn** over stdio, in its own process group, reaped on every exit path; no daemon, no socket, no WebSocket listener.
- Separate work principal `rooms-connector-codex`, bound only to `codex`; `codex` gets a teammate card and its legacy credential is revoked, with `codex-manual` for hand-posted notes (§3.4 unchanged, now applied to Codex too).

#### 3.6.2 Container boundary

| Item | Value |
|---|---|
| User | uid/gid 10001 (`rooms`), no shell login, no sudo |
| Root filesystem | `read_only: true`; `cap_drop: ALL`; `no-new-privileges`; `mem_limit 512m`; `pids_limit 128` |
| `/codex-home` | named volume `minimoi-staging-rooms-codex-home` (external), mode 700: `CODEX_HOME`, holding only the sign-in (`auth.json`) and Codex's own runtime files. No `config.toml`, `AGENTS.md`, `rules/`, `skills/`, `plugins/`, `hooks` — checked before every turn (§3.6.5) |
| `/state` | named volume `minimoi-staging-rooms-codex-state` (external): the turn journal and proof records |
| `/run/secrets/rooms` | named volume `minimoi-staging-rooms-codex-secrets` mounted **read-only**: `rooms-connector-codex.token`, `codex.token` (uid 10001, mode 600), copied in by `records.sh provision-codex` (a host bind would keep the Mac user's ownership, unreadable to uid 10001); `codex-manual.token` stays on the Mac in `secrets/rooms-codex-manual/` |
| `/turns`, `/tmp` | tmpfs (`/turns` 8 MB, mode 700): each turn's empty working directory |
| Not mounted | the repository, Robert's home, `~/.codex`, Docker socket, any other secret |
| Networks | `records-net` (internal) and a new ordinary bridge `minimoi-staging-rooms-codex-egress` (outbound HTTPS for ChatGPT). No published port. Records gains nothing. |
| Environment | `RECORDS_URL`, `ROOMS_TEAMMATE=codex`, `CODEX_HOME=/codex-home`, paths only. No `OPENAI_*`, no API key, no env_file. |

The model's only file-reaching tool, `view_image`, is refused before any read (E8/E9); even if a future catalog change let it run, it would see only this container (no Robert data) and cannot carry text such as `auth.json` (E6).

#### 3.6.3 Command and configuration (the flag profile)

```
codex app-server --listen stdio:// \
  --disable shell_tool --disable unified_exec --disable apps --disable plugins --disable browser_use \
  --disable browser_use_external --disable computer_use --disable in_app_browser --disable image_generation \
  --disable multi_agent --disable hooks --disable skill_search --disable skill_mcp_dependency_install \
  --disable tool_suggest --disable goals --disable code_mode_host --disable workspace_dependencies \
  --disable shell_snapshot --disable remote_plugin \
  -c web_search="disabled" -c skills.bundled.enabled=false -c skills.include_instructions=false \
  -c model_catalog_json="/app/services/rooms_connector/codex_catalog.json" -c model="gpt-5.5" \
  -c approval_policy="never" -c sandbox_mode="read-only" -c forced_login_method="chatgpt" \
  -c cli_auth_credentials_store="file"
```

JSON-RPC: `initialize` (`clientInfo.name = "minimoi-rooms"`) → `thread/start {cwd: <empty turn dir>, ephemeral: true, approvalPolicy: "never", sandbox: "read-only", baseInstructions: null, developerInstructions: <etiquette + brief>}` → `turn/start {threadId, input: [{type: "text", text: <attributed transcript JSON + trigger>}]}`. Stripped child environment: `HOME=/codex-home`, `CODEX_HOME=/codex-home`, `PATH=/usr/local/bin:/usr/bin:/bin`, `LANG`. The **profile hash** is SHA-256 over this exact argument list plus the catalog file's SHA-256.

#### 3.6.4 Turn contract (request, output, stop, recovery)

| Event | Handling |
|---|---|
| Notifications allowed | `thread/started`, `turn/started`, `item/started`/`item/completed` for item types **`userMessage`, `agentMessage`, `reasoning`, `plan`** only; `item/agentMessage/delta`, reasoning deltas, `thread/tokenUsage/updated`, `turn/completed`, `warning`, `configWarning`, status/rate-limit/account notifications |
| Any other item type (`commandExecution`, `fileChange`, `mcpToolCall`, `dynamicToolCall`, `collabAgentToolCall`, `subAgentActivity`, `webSearch`, `imageView`, `imageGeneration`, `hookPrompt`, …) | `turn/interrupt`, then kill the group; `uncertain`, reason `runner_boundary`; teammate *away* until proven again |
| Any server→client **request** (approvals, `item/tool/requestUserInput`, `item/tool/call`, elicitation, permissions, `account/chatgptAuthTokens/refresh`, `attestation/generate`) | answered with a JSON-RPC error ("declined by Rooms"), then the same as above |
| Answer | on `turn/completed` with `status: completed`: the text of the **last completed `agentMessage`**; non-empty → delivered (journaled first); empty → `failed`, `empty_reply` |
| Usage | the last `thread/tokenUsage/updated` for the turn, reported as `prompt_tokens`/`completion_tokens`; it is evidence the model worked |
| `turn/completed` `status: failed`, `codexErrorInfo: unauthorized` (or `httpConnectionFailed` 401) | with **no** agent output and **no** usage: `refused`, `signed_out` (definitive, the R2 P2 rule); otherwise `uncertain`, `signed_out_after_start` |
| `usageLimitExceeded` before any output/usage | `refused`, `usage_limit` ("Codex's ChatGPT plan limit is reached") |
| Other failure, malformed JSON-RPC, a non-object line, exit before `turn/completed` | `uncertain` |
| Stop | `turn/interrupt`, then TERM the group, KILL after 5 s; late output discarded (R1 rule) |
| Limits | 180 s per turn (kill → `uncertain`, `timeout`); 1 MB captured output; one turn at a time |
| Recovery | unchanged R1 §3.7: journal before start, receipt-first, never re-runs inference |

#### 3.6.5 Readiness, sign-in and proof

- **Sign-in (owner-run, once):** `scripts/staging/codex-room.sh login` runs `codex login --device-auth` inside the container with the same `CODEX_HOME`; Robert opens the shown URL and enters the code with his ChatGPT account. The login never leaves the volume; nothing is copied from the Mac's `~/.codex`. `codex-room.sh logout` removes it.
- **No-inference checks** (connector start, housekeeping and every turn): binary path, SHA-256 and version; `codex login status` reports ChatGPT sign-in (an API-key sign-in or none → `signed_out`, no process); `CODEX_HOME` contains only allowlisted entries (no `config.toml`, `AGENTS.md`, `rules`, `skills`, `plugins`, `hooks`; anything unreadable fails closed → `startup_inputs`); the profile hash.
- **Boundary probe (no model call):** at connector start and before a Prove, the runner starts the same command against a loopback fake endpoint inside the container (a `model_provider` override aimed at `127.0.0.1`, dummy key, scratch `CODEX_HOME`) and requires the captured request to offer exactly `{update_plan, request_user_input, view_image}`, a developer input without `skills_instructions`, and a scripted `view_image` call refused. A mismatch → `unready: runner_boundary`.
- **Proof:** as §3.7 (pending fingerprint before the process, promoted only after Records accepts the reply), fingerprint = binary SHA-256 + version + profile hash. `proof-codex.json` in `/state`. Any change → *away: Codex changed since its proof — Prove again*.

#### 3.6.6 Room experience (same as Claude Code)

Codex appears in Invite with its real state; `@Codex` routes to it (R1 routing, by card and host); failures use the R2 plain texts with a per-teammate sign-in hint (Claude Code: `claude auth login` on the Mac; Codex: `codex-room.sh login`).

### 3.7 Proof lifecycle (R2-06)

- **No-inference checks** (connector start and every turn): CLI path, its SHA-256 and `--version`; `auth status`; the startup-input allowlist; the flag profile (a hash of the exact argument list minus prompt text).
- **Evidence:** the one owner-approved **Prove** turn runs the normal command; the runner inspects `system/init` inside that same execution. The fingerprint of the binary about to run is written per turn (`proof-pending/<turn>.json`) **before** the process starts; it is promoted to the proof record only after Records accepted that proof reply (directly, through recovery, or found by receipt in housekeeping after a restart), and is never synthesized from whatever binary is installed later. On success the connector writes `~/minimoi-staging/data/rooms-connector/proof-claude-code.json` (binary SHA-256, version, profile hash, proof turn id, time) and Records sets `proven_at` as in R1.
- **Invalidation:** if the binary hash, version or profile hash differs from the proof record, the connector reports `unready: runner_changed_since_proof` to Records (stored as the teammate's reach reason, shown as "away: Claude Code changed since its proof — Prove again") and fails any claimed non-proof turn definitively before starting a process. Restarts never spend: they re-run only the no-inference checks.
- One execution, not two: the init check is part of the Prove turn, not a separate request.

## 4. Records changes

- `fence_append`: identity-based teammate detection (§3.4).
- `mark_reachable` accepts an optional `unready` reason code; stored on the card (`teammates.last_failure_at` unchanged; a new table `teammate_status(principal PK, unready, observed_at)`, new table only) and shown by `reach()`.
- `manage.py provision-rooms`: `--worker-principal`, several teammates, revocation of a teammate's legacy credential with an explicit list in the output, creation of a `<teammate>-manual` principal on request.
- **v0.4:** no Records code change. `records.sh provision-codex` reuses `provision-rooms` (`--teammate codex --worker-principal rooms-connector-codex --revoke-legacy --manual --card …`).

## 5. Out of scope

Grok (next runner); tools, repository or file access from a meeting turn; laptop-off participation; CoS; rounds; any tunnel route or public hostname; voice; a second human; cost reporting; production.

## 6. Tests (no model call)

| Layer | Tests |
|---|---|
| Records | identity fence (legacy token, no-origin, paused, stale generation); `-manual` principal posts as itself; `unready` reason stored and shown; worker bindings exclusive; provisioning output lists the revoked legacy credential |
| Connector | R1 worker suite re-run with a fake CLI (a script emitting stream-json): journal before start; stale admission re-asked; stop before spawn, during run, after output; timeout and output cap kill the group; no-output non-zero exit → `uncertain`; boundary-violating init → killed, `uncertain`; startup-input allowlist each case → `failed`, no process; runner change → `unready`, claimed turn failed before spawn; per-teammate adapter identity in export; recovery ignores another teammate's uncertain turns; housekeeping thread keeps reach fresh during a long fake turn |
| Door | forwards only to Records; LAN address refused; unauthenticated 401; published only on 127.0.0.1; no credential in its environment |
| Codex runner (v0.4) | a fake `codex app-server` (stdio JSON-RPC script): answer from the last agentMessage; each non-message item type and each server request → interrupt, `runner_boundary`; unauthorized with/without prior output or usage → `signed_out` / `signed_out_after_start`; stop → interrupt then kill; timeout; output cap; disallowed `CODEX_HOME` entries → `startup_inputs`; API-key login → `signed_out`; profile hash covers flags and catalog; proof pending → promoted |
| Container (v0.4) | preflight: user 10001, read-only root, no published port, networks exactly records-net + codex-egress, mounts exactly as §3.6.2, no credential variable |
| Browser | Invite lists Claude Code with its real state; `@Claude` → a fake-connector reply attributed to Claude Code; `@Codex` → a fake-runner reply attributed to Codex |

## 7. Gates

**v0.4:** G-R2C-0 Codex reviews this amendment; G-R2C-1 build, tests green, Codex reviews the frozen diff, dev rollout of `rooms-codex` (no model call); G-R2C-2 owner-run `codex-room.sh login`, then owner-approved Prove and the three-teammate meeting. Volumes `minimoi-staging-rooms-codex-{home,state,secrets}` are external, created and handed to uid 10001 by `provision-codex`. Rollback: stop and remove `rooms-codex`, revoke `rooms-connector-codex` and `codex` credentials. G-R2-0 Codex rechecks this draft. G-R2-1 build, tests green, Codex reviews the frozen diff, dev rollout of the door and connector (no model call). G-R2-2 owner-approved live turns: one Prove (with the init check inside it), one question, one stop mid-reply, connector stopped → *away*. **Rollback:** `connector.sh uninstall`, revoke the connector and teammate credentials, remove `records-door` and its network; R1 untouched.
