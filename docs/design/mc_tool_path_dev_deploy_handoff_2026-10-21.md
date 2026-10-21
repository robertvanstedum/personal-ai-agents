# MC tool path: what to deploy to dev (21 Oct 2026, after review)

**From:** Claude Code (side session "MC tool path"). **For:** the dev operator (Claude Code, parent session).
**Nothing here is deployed:** no dev change, no production, no merge. Robert is deciding on the gateway change (step 4).

**Design:** `docs/design/mc_tool_path_design_2026-10-21.md`. Copy both files into `planning-studio/initiatives/INIT-2026-0007-interaction-vision/mc-onboarding/handoffs/` (this worktree session may not write the shared checkout).
**Review:** `mc_tool_path_review.md` (commit `344d0a0c`). Every P1 and P2 is fixed below; the P3s are fixed or done.
**Branch:** `claude/mc-tool-path-86de08`, now on top of the `claude/dev-journeys-20261021` tip (`7d124112`, merged as `dcd899d1`).

## What it does

Robert speaks plainly; MC (native `mc-chat`) decides and calls nine tools:
- **Read:** host health; guest list (refs, app and date only); GitHub issues (a search, or one issue with its comments).
- **Proposals:** a guest reject; an issue comment; a data-repair change record (draft RFC); a lesson; delegated research (Claude Code only); a Meu Português login.

Each call is bound to Robert's **live turn-ledger claim**, never to identity the model supplies. Every proposal goes through C06 receipts and C07 grants, and **the platform shows it to Robert word for word, as its own note with a code (P-XXXX)**. Only his own reply approves it: a named "yes P-XXXX", or a plain "yes" as his very next message where that is allowed (below). Approving guests is designed (design §6), not shipped.

## Review fixes (one commit per finding)

| Finding | Commit | What changed | Regression test |
|---|---|---|---|
| P3-2 branch behind | `dcd899d1` | merged the dev-journeys tip; guest reject now uses `reject_guild_in` | the whole suite |
| P2-1 retried yes | `6c1ebffd` | a retried yes resolves to what that note confirmed, never a newer proposal | `test_p2_1_…` |
| P2-2 30-minute capture | `54131967` | short words are claimed only while a proposal is live, or for a retry of the same note | `test_p2_2_…` |
| **P1-1** uninformed yes | `fd383b7e` | the platform posts each proposal verbatim with a code; a plain yes counts only as the next turn and never after a GitHub read; one proposal per turn; a proposal that can't be shown is withdrawn | `test_p1_1_…` (3) |
| **P1-2** forgeable binding | `d09e820d` | the guest list carries no names or emails; until `MINIMOI_MC_TOOLS_RUNTIME_ISOLATED=1`, every proposal needs a named yes; the design states the limit plainly (also P3-5) | `test_p1_2_…` |
| P2-3 lesson/tool collision | `7807ee2c` | one register: lessons proposed by MC wait in `mc_tool_proposals`; a new proposal withdraws a waiting guest or lesson proposal from the phrase commands | `test_p2_3_…` |
| P2-4 public-text gaps | `59182654` | ASCII-only after NFKC; spaced or dotted letters rejoined; guest logins checked; "robert" no longer exempt | `test_p2_4_…` |
| P2-5 unverified gateway change; P1-2 follow-up | `265943a0` | `register()` never throws; the plugin offers no tools unless the run is `workspaceOnly` and the token is outside the workspace; the probe gains three phases | plugin tests; probe phases 2–4 |
| P2-6 change control | `6c9f4a93` | on prod, a guest reject or invite only as a pre-approved standard change, logged on its ticket; the data-repair ticket is a draft RFC with the §4 fields | `test_p2_6_…` (3) |
| P3-1, P3-3, P3-4 | `d5dad889` | loopback bind outside a container; research to Claude Code only; an invite never rewrites a guest's own request | `test_p3_3_…`, `test_p3_4_…` |

## Evidence

| Check | Result |
|---|---|
| `tests/mc_tools/run_postgres.sh <pgbin>` (real PostgreSQL 16.2, under `heavy_job.py`) | **56 OK**, no skips (41 before the review) |
| The reviewer's 6 reproductions, run once against the fixed code | T2, T3 and T6 now fail as they should (nothing posted or approved). T4 and T5 error on the changed `has_pending`/`parse` signatures; their behaviour is covered by `test_p2_2_…` and `test_p2_3_…`. T1 still lists and proposes, but leaks nothing, and its proposal needs a named yes. |
| `node --test tests/mc_tools/test_minimoi_tools_plugin.mjs` | **10 pass** |
| **Real runtime**, `node tests/mc_tools/openclaw_native_tool_probe.mjs` (installed OpenClaw 2026.9.4, isolated gateways, fake provider, Node 24.18, under `heavy_job.py`) | **4 phases pass.** (1) mc-chat is offered exactly the nine tools; the plugin sends `agent:mc-chat:openai-user:guild-mc:<32 hex>`, `mc-chat` and the bearer; `main` is offered none. (2) With Robert's nine real plugins enabled by name (no credentials), mc-chat's other tools are still only `read`, `ls`, `dir_list`. (3) With the token missing, the gateway starts and both agents answer; no MiniMoi tool; one log line. (4) Under OpenClaw's default file policy, no MiniMoi tool. Limit: in phase 2 the other plugins ran without their credentials, so a channel plugin that registers tools only once configured was not exercised. |
| Guild regression (`tests/guild tests/guild_ui tests/mc_lessons tests/mc_tools tests/mc_domain_actions tests/mc_delegation_runner`, under `heavy_job.py`) | **3,103 passed, 125 skipped**, 0 failed (the skips are real-database tests, run separately above) |

## Deploy steps (dev only)

### 1. Database (additive)

Apply `core/mc_tools/sql/001_tool_turns.sql` to the dev guild database as `minimoi`. It needs `mc_turn_ledger` (007) and the receipt and grant tables (004, 005), all already on dev. It adds `mc_tool_turns`, `mc_tool_proposals` and `mc_tool_evidence`. The file changed in review (new proposal columns and kinds); nothing was applied anywhere before.

### 2. Executor credential (never printed)

```bash
mkdir -p -m 700 ~/minimoi-runtime/mc-tools && (umask 077 && python3 -c "import secrets;print(secrets.token_urlsafe(32))" > ~/minimoi-runtime/mc-tools/executor.token)
```

```bash
python3 -c "import hashlib,pathlib;print(hashlib.sha256(pathlib.Path('~/minimoi-runtime/mc-tools/executor.token').expanduser().read_text().strip().encode()).hexdigest())"
```

The second command prints only the SHA-256, for the portal's environment. Under option A (step 4), create the file as the dedicated user instead, in that user's home.

### 3. Portal (overlay on the running dev image)

```
COPY minimoi_portal/guild_ui/ /app/minimoi_portal/guild_ui/
COPY core/ /app/core/
COPY services/mc_tools/ /app/services/mc_tools/
```

`minimoi_portal/app.py` changed by four lines (the invite email and Telegram delivery). Copy it only if the running image's `app.py` equals this branch's parent version; otherwise invites stay off by themselves.

```
environment:
  MINIMOI_MC_TOOLS: "1"
  MINIMOI_MC_TOOLS_ENV: dev
  MINIMOI_MC_TOOLS_PRINCIPAL: robert
  MINIMOI_MC_TOOLS_TOKEN_SHA256: <from step 2>
  MINIMOI_MC_TOOLS_PORT: "5011"
  MINIMOI_MC_TOOLS_COMMENTS: "1"              # with the issue credential below
  MINIMOI_MC_TOOLS_TICKETS: "1"               # with the issue credential below
  MINIMOI_MC_ISSUES_TOKEN_FILE: /run/secrets/mc-issues/token
  # MINIMOI_MC_TOOLS_RUNTIME_ISOLATED: "1"   # ONLY under option A (step 4). Without it every proposal needs "yes P-XXXX".
  # MINIMOI_MC_TOOLS_INVITES: "1"            # only with Robert's OK: on dev this creates a dev.minimoi.ai login and emails it
  # Guest rejects follow MINIMOI_MC_GUEST_ACTIONS=1 (already on). GitHub reads are on unless MINIMOI_MC_TOOLS_GITHUB=0.
  # Production only: MINIMOI_MC_TOOLS_STANDARD_CHANGES="guest_reject=<runbook id>@<ticket>"; without it prod refuses.
ports:
  - "127.0.0.1:5011:5011"                     # Mac loopback only; the Cloudflare tunnel stays on :5001
```

**Issue credential.** A fine-grained GitHub token for `robertvanstedum/personal-ai-agents`, with Issues read and write only, in a `0600` file mounted read-only into the portal. Robert creates it and types it in himself. Keep it out of any directory a command-running agent can read: for example a Docker volume rather than a file under `~`, or under option A outside the gateway user's reach.

After `up`: the portal log shows `mc tools: listener on 0.0.0.0:5011 (agents mc-chat)` (inside the container), and `curl -s http://127.0.0.1:5011/healthz` answers `{"status":"enabled"}`. The turn ledger must stay on.

### 4. The MC runtime: Robert's decision

The review's P1-2 stands: the executor token is a real boundary only when no command-running agent shares its OS user. The plugin itself now refuses to offer tools unless `mc-chat`'s file tools are confined to its workspace and the token lies outside it. Two options:

**A. Recommended: a separate gateway for `mc-chat` under its own macOS user.**
1. Create a dedicated user (for example `_mcchat`). In its home, install a second OpenClaw gateway on another loopback port with **only** the `mc-chat` agent (no `main`, no `mc-agent`, nothing that runs commands).
2. Give the agent `tools.fs.workspaceOnly: true`, with what it reads (the repository copy) inside its workspace, and the token outside it, owned by that user.
3. Point the native relay (`services/mc_operations/relay.py --config …`) at that gateway.
4. Install the plugin (step 5) there.
5. Set `MINIMOI_MC_TOOLS_RUNTIME_ISOLATED=1`; plain "yes" then works as the next message.

**B. Interim on Robert's own gateway (dev only, risk accepted in writing).** Install the plugin as in step 5 with `"allowTokenReadableByAgent": true` in its config, and leave `MINIMOI_MC_TOOLS_RUNTIME_ISOLATED` unset. Then:
- every proposal needs "yes P-XXXX";
- the guest list holds no personal data;
- any agent of that gateway could still read the token and offer proposals, which Robert would see and could refuse.

Do not use B for production.

### 5. Plugin installation (on the gateway chosen in step 4; back up its `openclaw.json` first)

1. Copy `services/mc_operations/plugins/minimoi-tools/` to a stable place (not the shared checkout).
2. Change exactly:
   - `plugins.load.paths`: add the plugin directory;
   - `plugins.entries["minimoi-tools"]`: `{"enabled": true, "config": {"enabled": true, "endpoint": "http://127.0.0.1:5011/internal/mc/v1/tools", "tokenFile": "<absolute token path>", "agents": ["mc-chat"], "diskPaths": ["/System/Volumes/Data"], "docker": "/opt/homebrew/bin/docker"}}` (plus `"allowTokenReadableByAgent": true` only under option B);
   - `agents.entries["mc-chat"].tools`: remove `"group:plugins"` from `deny`, add `"minimoi-tools"` to `alsoAllow` (exact result: `tests/mc_tools/fixtures/mc_chat_tools_policy.json`), and under option A add `"fs": {"workspaceOnly": true}`.
   - **Do not add `plugins.allow`** (it is an allowlist and would stop Robert's other plugins).
3. Run `openclaw config validate`, then restart that gateway.
4. Append `MC_CHAT_AGENTS_SECTION.md` to that `mc-chat` workspace's `AGENTS.md` (back it up first).

If the token is missing or the configuration is wrong, the plugin logs `minimoi-tools: not registered …` and the gateway carries on (probe phase 3).

### 6. Verify on dev

- Re-run the probe: `node tests/mc_tools/openclaw_native_tool_probe.mjs` (isolated; cleans up its temporary state).
- In the Guild conversation, as Robert:
  - "hey MC, what's our disk space" → answered from `minimoi_host_health`.
  - "do we have guest requests? I can't approve them" → MC lists refs, may cite #69, and offers to reject one. A platform note shows **who** and the code; "yes P-XXXX" (or plain "yes" next, under option A) → rejected and verified by read-back.
  - "document that workaround on #69" → the platform note shows the exact public comment. After a GitHub read in that turn, only "yes P-XXXX" approves.
  - "remember: …" via MC → a platform note; "yes" → kept. (The "Remember: …" phrase command keeps its own "Confirm".)
  - "can you get Claude Code to look into …" → a platform note; "yes" → the job starts from Robert's own message.
- Portal log: `mc tool <name> -> <status>/<code>`. Database: `SELECT capability, state FROM guild.mc_action_receipts ORDER BY created_at DESC LIMIT 10`.

## Rollback

1. Native: restore the gateway's `openclaw.json` and `AGENTS.md` backups and restart it (under A, stop the dedicated gateway and point the relay back).
2. Portal: `compose.py up` without the tools overlay. With `MINIMOI_MC_TOOLS` unset nothing in this change runs.
3. The tables are additive; leave them.

## Where I differ from the review

- **P1-1, "untrusted text can't lead to a proposal without Robert's own words asking".** I did not try to infer intent from Robert's words; that would be a parser deciding authority. Instead, a proposal made after a GitHub read in the same turn is shown verbatim and needs a **named** yes. Robert keeps full control, and a steered proposal can't ride on a reflexive "ok". The guest list no longer carries guest text (P1-2), so only GitHub is treated as untrusted.
- **P1-2 is not closable in code** while `toolBindings` can't reach plugins (N1, RC9-F1). I agree with the review's fix (option A) and made the code fail safe without it: no personal data out, a named yes always, and no tools when the agent could read the token. This is defence in depth, not the boundary, and the design says so.
- **P2-4, templated public text.** I kept a short free-text summary, because a workaround note on #69 needs words. It is shown verbatim (P1-1), ASCII-only, and filtered against requests and logins. If Robert prefers templates only, removing `summary` is a small change.
- **P2-6:** I treated issue comments, data-repair RFC drafts, lessons and delegated research as non-production changes under the standard (§2: tickets and reading aren't changes; lessons shape MC's answers through the product's own flow; research is read-only). Only data (guest reject) and access (invite) are gated on prod. Say so if Robert wants lessons gated too.
- **P3-4:** creating a new request row for an owner invite isn't possible, because `guest_requests.email` is unique. Instead the invite refuses and leaves the guest's own record untouched.
