# Rooms R2 — Claude Code joins a room from this Mac · specification v0.3 (build candidate)

**Edition:** v0.3 · 1 October 2026 · **Author:** Claude Code (sole implementation editor) · **Reviewer:** Codex (independent) · **Decision owner:** Robert
**Status:** Codex cleared v0.2 for the Claude-only implementation (`CODEX_RECHECK_R2_R3_v0.2_2026-10-01.md`); v0.3 folds in its four acceptance clarifications and two adjustments found while building (§0). v0.2 answered Codex's review (`CODEX_REVIEW_R1_PREFLIGHT_R2_R3_2026-10-01.md`, R2-01…R2-06). Robert authorized R2 on dev on 1 October 2026; production, main merges and paid/live proofs keep their gates.
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

## 1. Outcome

In a Rooms meeting Robert invites **Claude Code** from the Invite dialog. Addressed with `@Claude` (or its recipient chip), Claude Code answers one bounded, tool-free discussion turn, attributed to `claude-code`, through Robert's existing Claude subscription on this Mac. No API key, no tool, no file access from the model. When the connector stops or its runner changes, Claude Code shows *away* with the real reason within three minutes; when the Mac sleeps the whole dev site is down (R1 §3.9 case a).

R2 proves: one in-room turn from Claude Code, stop discards late output, connector loss shows *away*. It does not prove: Codex in Rooms (parked), tools or repository work from a meeting, laptop-off participation, CoS, rounds, voice, production.

## 2. Verified facts (1 October 2026, read-only, no model call)

| Fact | Evidence |
|---|---|
| Claude Code 2.1.76 at `/opt/homebrew/bin/claude`; `claude auth status`: signed in through claude.ai, Pro; no `ANTHROPIC_API_KEY` in the environment | CLI output |
| With an empty dedicated `HOME`, `claude auth status` reports **signed out**: the subscription sign-in is tied to Robert's real home, so the runner keeps `HOME` and isolates by other means | CLI output |
| Robert's user-level Claude configuration holds no instruction file (`~/.claude/CLAUDE.md` and `~/.claude/rules` absent) and `settings.json` holds only display preferences | file listing |
| Headless flags present: `-p`, `--output-format json|stream-json`, `--tools ""`, `--strict-mcp-config`, `--mcp-config`, `--setting-sources`, `--system-prompt`, `--no-session-persistence`, `--disable-slash-commands` | `claude --help` |
| Codex (ChatGPT-app bundled 0.158.0-alpha.2): `codex debug prompt-input` renders the model-visible input without a model call; it includes local skills instructions and a multi-agent role, and **no tool list**; feature flags (`shell_tool`, `unified_exec`, `plugins`, `apps`, `browser_use`, `computer_use`, …) exist but their effective result is not shown | CLI output |
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

### 3.6 Codex: parked, fail-closed

No Codex runner is wired in R2. Codex's card is not created, so `@Codex` keeps getting R1's "Not sent to Codex: not available in Rooms yet". Unparking needs: an inspectable effective-tool listing showing no file, shell, browser, computer, plugin or app tools; a model-visible input without local skills, memory or agent-role instructions; and a sign-in that the model's tools cannot reach. The evidence above shows none of these today without a model call.

### 3.7 Proof lifecycle (R2-06)

- **No-inference checks** (connector start and every turn): CLI path, its SHA-256 and `--version`; `auth status`; the startup-input allowlist; the flag profile (a hash of the exact argument list minus prompt text).
- **Evidence:** the one owner-approved **Prove** turn runs the normal command; the runner inspects `system/init` inside that same execution. The fingerprint of the binary about to run is written per turn (`proof-pending/<turn>.json`) **before** the process starts; it is promoted to the proof record only after Records accepted that proof reply (directly, through recovery, or found by receipt in housekeeping after a restart), and is never synthesized from whatever binary is installed later. On success the connector writes `~/minimoi-staging/data/rooms-connector/proof-claude-code.json` (binary SHA-256, version, profile hash, proof turn id, time) and Records sets `proven_at` as in R1.
- **Invalidation:** if the binary hash, version or profile hash differs from the proof record, the connector reports `unready: runner_changed_since_proof` to Records (stored as the teammate's reach reason, shown as "away: Claude Code changed since its proof — Prove again") and fails any claimed non-proof turn definitively before starting a process. Restarts never spend: they re-run only the no-inference checks.
- One execution, not two: the init check is part of the Prove turn, not a separate request.

## 4. Records changes

- `fence_append`: identity-based teammate detection (§3.4).
- `mark_reachable` accepts an optional `unready` reason code; stored on the card (`teammates.last_failure_at` unchanged; a new table `teammate_status(principal PK, unready, observed_at)`, new table only) and shown by `reach()`.
- `manage.py provision-rooms`: `--worker-principal`, several teammates, revocation of a teammate's legacy credential with an explicit list in the output, creation of a `<teammate>-manual` principal on request.

## 5. Out of scope

Codex in Rooms; tools, repository or file access from a meeting turn; laptop-off participation; CoS; rounds; any tunnel route or public hostname; voice; a second human; cost reporting; production.

## 6. Tests (no model call)

| Layer | Tests |
|---|---|
| Records | identity fence (legacy token, no-origin, paused, stale generation); `-manual` principal posts as itself; `unready` reason stored and shown; worker bindings exclusive; provisioning output lists the revoked legacy credential |
| Connector | R1 worker suite re-run with a fake CLI (a script emitting stream-json): journal before start; stale admission re-asked; stop before spawn, during run, after output; timeout and output cap kill the group; no-output non-zero exit → `uncertain`; boundary-violating init → killed, `uncertain`; startup-input allowlist each case → `failed`, no process; runner change → `unready`, claimed turn failed before spawn; per-teammate adapter identity in export; recovery ignores another teammate's uncertain turns; housekeeping thread keeps reach fresh during a long fake turn |
| Door | forwards only to Records; LAN address refused; unauthenticated 401; published only on 127.0.0.1; no credential in its environment |
| Browser | Invite lists Claude Code with its real state; `@Claude` → a fake-connector reply attributed to Claude Code; `@Codex` → the R1 not-available line |

## 7. Gates

G-R2-0 Codex rechecks this draft. G-R2-1 build, tests green, Codex reviews the frozen diff, dev rollout of the door and connector (no model call). G-R2-2 owner-approved live turns: one Prove (with the init check inside it), one question, one stop mid-reply, connector stopped → *away*. **Rollback:** `connector.sh uninstall`, revoke the connector and teammate credentials, remove `records-door` and its network; R1 untouched.
