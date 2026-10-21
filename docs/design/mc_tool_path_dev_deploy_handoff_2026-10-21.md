# MC tool path: what to deploy to dev (21 Oct 2026)

**From:** Claude Code (side session "MC tool path"). **For:** the dev operator (Claude Code, parent session).
**Nothing here is deployed:** no dev change, no production, no merge.

**Design:** `docs/design/mc_tool_path_design_2026-10-21.md`. Copy both files into `planning-studio/initiatives/INIT-2026-0007-interaction-vision/mc-onboarding/handoffs/` (this worktree session is not allowed to write the shared checkout).
**Branch:** `claude/mc-tool-path-86de08`, on top of `claude/dev-journeys-20261021` (`cd5ba545`).

## What it does

Robert speaks plainly; MC (native `mc-chat`) decides and calls six tools: host health, guest list (read-only), propose a data-repair ticket, propose a lesson, propose delegated research, propose a Meu Português login. Per Robert's scope change (21 Oct), MC does not reject guest requests: it points him to Operate and, when that cannot do it, offers a ticket that names refs only. Each call is bound to Robert's **live turn-ledger claim**, never to identity the model supplies. Effects go through C06 receipts and C07 grants; consequential ones wait for Robert's own **yes** (or Confirm) in the same conversation. The owner-command phrases still answer first.

## Evidence

| Check | Result |
|---|---|
| `tests/mc_tools/run_postgres.sh <pgbin>` (real PostgreSQL 16.2) | 36 OK, no skips |
| `tests/mc_domain_actions/run_postgres.sh <pgbin>` (guest refactor) | 36 OK |
| `node --test tests/mc_tools/test_minimoi_tools_plugin.mjs` | 8 pass |
| `python -m unittest tests/mc_tools/test_portal_wiring.py` (includes the GitHub filer) | 6 OK |
| **Real runtime:** `node tests/mc_tools/openclaw_native_tool_probe.mjs` (installed OpenClaw 2026.9.4, isolated gateway, fake provider, Node 24.18) | PASS (rerun after the scope change). mc-chat is offered exactly the six tools plus its existing `read`, `ls`, `dir_list`. The plugin sent `agent:mc-chat:openai-user:guild-mc:<32 hex>`, agent `mc-chat` and the executor bearer. `main` is offered none. |
| Real host measurement through the plugin and `core/mc_tools/host.py` | 8.6 GB free of 228.3 GB (96.2% used); 8 containers running |
| Guild regression (`pytest tests/guild tests/guild_ui tests/mc_lessons tests/mc_tools`) | 3,050 passed, 77 skipped (the skips are the real-database tests, run separately above) |

## Deploy steps (dev only)

### 1. Database (additive)

Apply `core/mc_tools/sql/001_tool_turns.sql` to the dev guild database as `minimoi`, which owns the other `mc_*` tables. It needs `mc_turn_ledger` (007) and the receipt and grant tables (004, 005), all already on dev. It adds three tables: `mc_tool_turns`, `mc_tool_proposals`, `mc_tool_evidence`.

### 2. Executor credential (never printed)

```bash
mkdir -p -m 700 ~/minimoi-runtime/mc-tools && (umask 077 && python3 -c "import secrets;print(secrets.token_urlsafe(32))" > ~/minimoi-runtime/mc-tools/executor.token)
```

```bash
python3 -c "import hashlib,pathlib;print(hashlib.sha256(pathlib.Path('~/minimoi-runtime/mc-tools/executor.token').expanduser().read_text().strip().encode()).hexdigest())"
```

The second command prints only the SHA-256, which goes into the portal's environment. The file must stay `0600`, owned by the user that runs the native gateway, with no symlink anywhere in its path; the plugin refuses it otherwise.

### 3. Portal (overlay on the running dev image)

Add to the portal build overlay, as for the journeys:

```
COPY minimoi_portal/guild_ui/ /app/minimoi_portal/guild_ui/
COPY core/ /app/core/
COPY services/mc_tools/ /app/services/mc_tools/
```

`minimoi_portal/app.py` changed by four lines only: it registers the invite email and Telegram delivery. **Copy it only if** the running image's `app.py` equals this branch's parent version. Otherwise leave it: invites then stay off by themselves (the listener finds no delivery and tells MC so), and the other five tools do not need it.

Compose overlay additions for `portal`:

```
environment:
  MINIMOI_MC_TOOLS: "1"
  MINIMOI_MC_TOOLS_ENV: dev
  MINIMOI_MC_TOOLS_PRINCIPAL: robert          # the owner's portal username, as MINIMOI_MC_DELEGATION_PRINCIPAL
  MINIMOI_MC_TOOLS_TOKEN_SHA256: <from step 2>
  MINIMOI_MC_TOOLS_PORT: "5011"
  # MINIMOI_MC_TOOLS_INVITES: "1"            # only with Robert's OK: on dev this creates a dev.minimoi.ai login and emails it
  MINIMOI_MC_TOOLS_TICKETS: "1"
  MINIMOI_MC_ISSUES_TOKEN_FILE: /run/secrets/mc-issues/token   # see below; without it MC is told tickets are off
ports:
  - "127.0.0.1:5011:5011"                     # Mac loopback only; the Cloudflare tunnel stays on :5001
```

**Ticket credential.** A GitHub fine-grained token limited to `robertvanstedum/personal-ai-agents` with Issues: read and write only, in a `0600` file mounted read-only into the portal (for example `~/minimoi-staging/secrets/mc-issues/token` → `/run/secrets/mc-issues/token`). Robert creates it and types it in (credential handoff: never pasted into chat). The repository comes from `MINIMOI_GITHUB_REPO` (default `robertvanstedum/personal-ai-agents`). The issue names refs, the change, the environment and the operation id only.

The turn ledger (`MINIMOI_MC_TURN_LEDGER=1`) must stay on: without it no binding exists and every tool refuses. After `up`, check for the portal log line `mc tools: listener on port 5011 (agents mc-chat)`, and that `curl -s http://127.0.0.1:5011/healthz` answers `{"status":"enabled"}`.

### 4. Native OpenClaw (Robert's own gateway; back up first)

1. Copy the plugin from the commit to a stable place: `services/mc_operations/plugins/minimoi-tools/` → `~/minimoi-runtime/mc-tools/plugin/`, not the shared checkout, which moves.
2. Back up `~/.openclaw/openclaw.json`, then change exactly three things:
   - `plugins.load.paths`: add `/Users/vanstedum/minimoi-runtime/mc-tools/plugin`.
   - `plugins.entries["minimoi-tools"]`: `{"enabled": true, "config": {"enabled": true, "endpoint": "http://127.0.0.1:5011/internal/mc/v1/tools", "tokenFile": "/Users/vanstedum/minimoi-runtime/mc-tools/executor.token", "agents": ["mc-chat"], "diskPaths": ["/System/Volumes/Data"], "docker": "/opt/homebrew/bin/docker"}}`.
   - `agents.entries["mc-chat"].tools`: remove `"group:plugins"` from `deny` and add `"minimoi-tools"` to `alsoAllow`. The exact result is `tests/mc_tools/fixtures/mc_chat_tools_policy.json`. With the `minimal` profile no other plugin's tools become allowed; the probe prints the full list.
   - **Do not add `plugins.allow`.** It is an allowlist and would stop Robert's other plugins (Telegram, WhatsApp and the rest). The plugin loads without it; OpenClaw's warning that it cannot verify the source is expected for a local plugin.
3. Run `openclaw config validate`, then restart the gateway (launchd service `ai.openclaw.gateway`).
4. Append `services/mc_operations/plugins/minimoi-tools/MC_CHAT_AGENTS_SECTION.md` to `~/.openclaw/workspace-mc-chat/AGENTS.md` (back it up first). Without it, mc-chat's current instructions steer "disk space" to a `JOB:` line and research to `ASK`.

### 5. Verify on dev

- Re-run the probe against the installed runtime: `node tests/mc_tools/openclaw_native_tool_probe.mjs`. It is isolated and does not touch the live gateway.
- In the Guild conversation, as Robert:
  - "hey MC, what's our disk space" → MC answers from `minimoi_host_health` (about 8–9 GB free of 228 GB now: the Mac is at 96%).
  - "do we have guest requests? I can't reject them myself" → MC lists them, points to Operate, offers a ticket; "yes" → "Ticket N created" (needs the ticket credential), naming refs only.
  - "remember: …" → a proposal; "Confirm" → kept.
  - "can you get Claude Code to look into …" → a proposal; "Confirm" → the job starts from Robert's own message.
- Portal log: `mc tool <name> -> <status>/<code>`. Database: `SELECT capability, state FROM guild.mc_action_receipts ORDER BY created_at DESC LIMIT 10`.

## Rollback

1. Native: restore the `openclaw.json` and `AGENTS.md` backups and restart the gateway. MC loses the tools; nothing else changes.
2. Portal: `compose.py up` without the tools overlay, as for the journeys. With `MINIMOI_MC_TOOLS` unset, nothing in this change runs.
3. The tables are additive; leave them.

## Open items, for review

- **Binding strength.** This binds by session key and live ledger claim, not by Codex's per-run `toolBindings` (blocked by N1 and RC9-F1). The residual risk is in design §3. Swapping later changes only `core/mc_tools/binding.py`.
- **Delegation spend.** `delegate.start_research` is catalogued zero-spend because MiniMoi only queues a capped request; the runner enforces the $2 cap or refuses. Codex should confirm or reject this reading of C07.
- **Invites on dev** create dev logins and send real email. Leave them off unless Robert says so.
- **Plain "yes" claims.** `core/mc_tools/proposals.parse` takes a short plain yes/no only while a tool proposal waits in this conversation; guest and lesson commands still answer first.
- **Independent review** requested from Codex: `core/mc_tools/`, `services/mc_tools/`, the plugin, and the two `api.py` hooks (`_tool_binder`, `_owner_tool_proposal_command`).
