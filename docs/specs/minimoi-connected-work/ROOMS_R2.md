# Rooms R2 — Claude Code and Codex join from this Mac · specification v0.1 (review draft)

**Edition:** v0.1 · 1 October 2026 · **Author:** Claude Code (sole implementation editor) · **Reviewer:** Codex (independent) · **Decision owner:** Robert
**Status:** draft for Codex's review before any wiring. Robert authorized R2 on dev on 1 October 2026 ("I give approval to finish R2 and R3 as well"); production, main merges and paid live proofs keep their existing gates.
**Builds on:** [Rooms R1](ROOMS_R1.md) v0.5.1 (adopted), frozen R1 head `042239a0`. R2 changes no R1 contract; it adds a second hosted connector of the same kind as the MC worker. `records/` means `prototype-lab/projects/project-records-room-poc/`.

## 1. Outcome

In a Rooms meeting, Robert invites **Claude Code** (and, once its isolation proof passes, **Codex**) from the Invite dialog exactly as he invites Master Craftsman. Addressed with `@Claude` (or a recipient chip), Claude Code answers one bounded discussion turn in the room, attributed to `claude-code`, through Robert's existing Claude subscription on this Mac. Nothing new is installed in the cloud, no API key is used, no tool runs. When the Mac sleeps the whole dev site is down (the hub runs here too, R1 §3.9 case a); when only the connector stops, Claude Code shows *away: its worker has not checked in recently* within three minutes.

R2 proves: one in-room turn each from Claude Code (and Codex when proven isolated), stop discards late output, connector loss shows *away*. It does not prove: laptop-off participation, tools or repository work from a meeting, CoS, everyone-rounds, voice, a second human, production.

## 2. Verified facts this spec rests on (1 October 2026, read-only checks)

| Fact | Evidence |
|---|---|
| Claude Code 2.1.76 at `/opt/homebrew/bin/claude`, `claude auth status`: `loggedIn: true`, `authMethod: claude.ai`, `subscriptionType: pro`; no `ANTHROPIC_API_KEY` in the environment | CLI output |
| Its headless flags: `-p`, `--output-format json|stream-json`, `--tools ""` (disables every built-in tool), `--strict-mcp-config` with `--mcp-config`, `--setting-sources`, `--system-prompt`, `--no-session-persistence`, `--disable-slash-commands`; `--max-budget-usd` applies to API billing only | `claude --help` |
| Codex: the working CLI is the ChatGPT app's bundled `codex-cli 0.158.0-alpha.2`; `codex login status`: "Logged in using ChatGPT". The `~/.local/bin/codex` 0.145 cannot use the configured model | CLI output; R1 review runs |
| Codex `exec` flags: `-s read-only`, `--ephemeral`, `--ignore-user-config`, `-C`, `--skip-git-repo-check`, `--json`, `-o` | `codex exec --help` |
| Codex's `read-only` sandbox still lets its shell read any file: **not** private-data isolation (Codex review R1-06) | Codex documentation of the mode; Codex's own finding |
| macOS `sandbox-exec` confines reads: with a profile denying `file-read*` under `$HOME`, `cat` of a temp file succeeds and `cat ~/.zshrc` is refused | test with `cat`, not with Codex |
| The hub (container Records) has no host port today; a Mac process cannot reach it | R1 §1, compose |

## 3. Architecture

### 3.1 One hub door for Mac-hosted connectors

Records publishes **one loopback-only port**, `127.0.0.1:18881 → 18880`, in `docker-compose.records.yml`; `RECORDS_ALLOWED_HOSTS` adds `127.0.0.1:18881`. Nothing else changes in Records' boundary: it stays on `records-net` only (internal, no egress); the port is bound to loopback, so only processes on this Mac reach it; every route still needs a credential; the portal bridge is unchanged. This is the same door R3's direct-door slice will later put a front-door control in front of; R2 does **not** add a tunnel route or hostname.

### 3.2 The connector: the R1 worker with a CLI runner

`services/rooms_connector/` runs **the R1 worker loop unchanged** (`services/rooms_worker/worker.py`: claim, admission, journal, watcher, recovery, reconciliation) with a different runner in place of the relay client. A runner implements the relay client's three calls:

| Call | Claude Code runner | Codex runner (gated, §3.5) |
|---|---|---|
| `ready()` | CLI present at its pinned path and `claude auth status` reports `loggedIn: true` with `authMethod: claude.ai` and no API key in the runner's environment | bundled CLI present, `codex login status` reports ChatGPT sign-in, and the isolation proof (§3.5) passed on this connector start |
| `stream(messages, user, correlation)` | one subprocess per turn (below); returns the same outcome shapes as the relay (`done`, `refused`, `stopped`, `error`) | same shape |
| `stop(correlation)` | kills the turn's process group (TERM, then KILL after 5 s) | same |

The worker's other rules carry over as reviewed: journal before inference; no CLI start without a dispatch answer younger than 5 s; a stop request kills the process and the late output is journaled, never posted; transport/process failures after start are `uncertain`; the journal report gates Retry.

**Claude Code turn command (the effective boundary):**

```
env -i HOME=$HOME PATH=/usr/bin:/bin:/opt/homebrew/bin LANG=en_US.UTF-8 \
  /opt/homebrew/bin/claude -p --output-format json --tools "" \
  --strict-mcp-config --mcp-config '{"mcpServers":{}}' --setting-sources "" \
  --disable-slash-commands --no-session-persistence \
  --system-prompt "<etiquette + brief, R1 §3.10>"     # stdin: transcript JSON, then the trigger
```

run in a fresh empty temporary directory (mode 700) as working directory, so no project `CLAUDE.md` loads; no `ANTHROPIC_API_KEY` exists in the stripped environment, so there is no silent API fallback. **Effective-boundary evidence:** before the connector first marks Claude Code reachable, its preflight runs the same command with `--output-format stream-json --verbose` and reads the CLI's own `system/init` event: `tools` must be empty and `mcp_servers` empty; any other value keeps Claude Code *away* with that reason. The init check makes one minimal model request and is part of the owner-approved proof (§6), not a background check.

**Codex turn command (only after §3.5 passes):** the bundled CLI, `exec -s read-only --ephemeral --ignore-user-config --skip-git-repo-check -C <empty temp dir> -o <temp file>`, the prompt on stdin, wrapped in `sandbox-exec` with a profile that denies `file-read*` and `file-write*` under `$HOME` except the Codex sign-in files it needs (`~/.codex/auth.json` read-only) and the turn's temp directory, and denies network except the CLI's own API endpoint if the profile can express it (if not, network stays as the CLI's own; reads are the protected surface).

### 3.3 Identities and provisioning

- Teammates: the existing principals `claude-code` and `codex` (kind `agent`, in the container Records since 1 October). Their existing legacy credentials and the manual `roomctl` path stay as they are (M6 spirit: nothing that works today breaks); their posts without a turn are unaffected by the fence because a legacy credential is not membership-scoped and carries no agent-response origin.
- New: a teammate card for each (`host: this Mac`, `connector: rooms-connector`, `tools_profile: none`, `billing_route: Robert's Claude subscription` / `Robert's ChatGPT sign-in`, `max_turn_s: 180`, `auto_accept: 1`), a membership-scoped credential each, and a **separate** work-scoped service principal `rooms-connector-mac` bound to `claude-code` (and to `codex` only when §3.5 passes). The MC worker's binding is unchanged; neither worker can claim the other's teammates.
- `manage.py provision-rooms` gains `--worker-principal` and accepts several teammates; tokens go only to `0600` files in `~/minimoi-staging/secrets/rooms-connector/` (mode 700); nothing is printed.

### 3.4 Running on the Mac

- A launchd agent `com.vanstedum.minimoi-rooms-connector` (KeepAlive) runs the connector from a private virtualenv `~/minimoi-staging/connector-venv` (`requests` only). Journal: `~/minimoi-staging/data/rooms-connector-journal` (700). Log: `~/minimoi-staging/logs/rooms-connector.log` (ids and outcomes only).
- `scripts/staging/connector.sh install|uninstall|status|preflight` writes and removes the plist; `uninstall` leaves the journal and secrets in place and revokes nothing (revocation is a separate owner command, as in R1).
- One turn at a time per connector (the loop is sequential); `max_turn_s` 180 kills a turn and reports it `uncertain`.

### 3.5 Codex isolation proof (gate for the Codex runner)

Codex joins only when, on this Mac, a proof run shows: (1) the wrapped bundled CLI starts and answers a fixed one-line prompt (a model request; owner-approved, §6); (2) inside the same wrapper a shell command reading a canary file placed in `$HOME` fails; (3) reading the turn's temp directory works; (4) `~/.codex/sessions` gains no new file (`--ephemeral`). If (1) fails because the CLI cannot run under the wrapper, or (2) succeeds, Codex stays *away: no enforced read isolation yet* and R2 ships with Claude Code alone. No weaker boundary is substituted.

### 3.6 Records and Rooms changes

- Routing: `route()` already sends a turn only to card teammates with a connector; R2 adds no rule. Unaddressed messages still go to the facilitator (MC). `@Claude`, `@claude-code`, a chip, or `@Codex` address them.
- Reach: unchanged rules (proof, fresh readiness, no failure since the last commit). The connector reports readiness every 10 s; Mac sleep or connector stop → expiry after 180 s → *away* with the R1 reason.
- Usage evidence: the Claude CLI's JSON `usage` gives input/output token counts → `usage_evidence_status: reported` with those counts; Codex's `--json` usage events the same if present, else `none`. Nothing is invented; `upstream_execution_id` stays null; `caller_correlation` is the connector's own id.
- UI: no new screen. The Invite dialog lists the new cards with their real state ("Not yet proven — Prove first", "No connector in Rooms yet" for Codex until §3.5).

## 4. Out of scope

Tools, repository or file access from a meeting turn; laptop-off participation; Codex without its isolation proof; CoS; everyone-rounds; a tunnel route or public hostname; voice; a second human; cost reporting; production.

## 5. Tests (no model call)

| Layer | Tests |
|---|---|
| Records | loopback host accepted only as configured; `provision-rooms` with a second worker principal and two teammates; the MC worker cannot claim `claude-code` and the connector cannot claim `mc`; legacy `claude-code` posts without a turn unchanged |
| Connector | the R1 worker suite re-run with a fake CLI runner: journal before start, stale admission re-asked, stop kills the process group and discards late output, timeout → `uncertain`, non-zero exit before output → `refused` vs after start → `uncertain`; the built command has `--tools ""`, empty MCP, no setting sources, empty cwd, stripped environment without `ANTHROPIC_API_KEY`; `ready()` false when auth status says API key or logged out; Codex runner refuses to be enabled without a recorded passing proof |
| Compose/scripts | Records publishes only `127.0.0.1:18881`; production compose byte-identical; classifier staging-only for `services/rooms_connector/`; `connector.sh` writes no secret into the plist; preflight never counts a failed probe |
| Browser | Invite lists Claude Code with its real state; `@Claude` addressed message → a fake connector reply attributed to Claude Code |

## 6. Gates

- **G-R2-0:** Codex reviews this draft; findings resolved before wiring.
- **G-R2-1:** the reviewed build's tests green; Codex reviews the frozen diff.
- **G-R2-2 (owner-approved, live):** one Prove turn for Claude Code through the connector (the init-event check runs first in the same approval), one stop mid-reply, connector stop → *away*. Codex's isolation proof (§3.5) and its Prove turn likewise. These use Robert's subscriptions; they wait for his explicit OK.
- **Rollback:** `connector.sh uninstall`, revoke the connector and teammate credentials, remove the loopback port from compose and recreate Records (no data change).
