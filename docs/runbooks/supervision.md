# Runbook — build supervision: check-ins, nudges and Robert's approvals (#365)

**26 Oct 2026 · Claude Code.** Robert: "check in every 15 minutes on Claude and the build. If there is an issue or
error, or very high stake decision, let me know", and "I don't need MC to approve. I need to know that I need to approve
and tell me." This page covers what runs where, how to turn it on and off, and how to check it.

## What runs where

| Piece | Where | What it does |
|---|---|---|
| `scripts/agents/stop.py` | the Mac, run by Claude Code / Codex | `line` prints and files the stop line. `ask` files a decision for Robert. `wait` blocks until his answer or a nudge arrives (run in the background; its exit wakes a Claude Code session). |
| `scripts/agents/claude_stop_hook.py` | the Mac, Claude Code's Stop hook | files every turn's stop line, and asks once when it is missing. Settings: `scripts/agents/claude-stop-hook.settings.json` |
| `scripts/mc_host/supervision_sync.py` | the Mac, launchd `ai.minimoi.supervision-sync`, every 30 s | outbound HTTPS to `minimoi.ai/internal/supervision/v1/worker`: stops and asks up; answers and nudges down into `~/.minimoi/supervision/inbox/<session>/` |
| `/internal/supervision/v1/worker` | portal (`minimoi_portal/guild_ui/supervision.py`) | the sync's only path. Its token's SHA-256 is all prod holds. It can report and ask, never answer. |
| `minimoi-supervisor` | EC2, portal image, compose profile `supervisor` | sends each decision to Robert word for word on the approvals bot, records his reply, runs the check-in every 15 min (`core/supervision/watch.py`) |
| "approve D-n" / "no D-n" | the Guild chat (`_owner_decision_command`) | Robert's own owner turn answers that decision into the same record |
| MC persona 0.1.15 | MC's container | MC raises waiting decisions first, never approves, and pushes the rest forward |

Tables: `guild.supervision_*` (`core/supervision/sql/001_supervision.sql`, applied by `mc_prod_db_migrate.sh`).

## What counts as Robert's answer

- **Telegram:** a plain message from Robert's own user id, in his private chat with the approvals bot, sent as a
  reply to the exact message that carried the decision. Forwards, edits, other chats and other senders never count.
  An approval is the whole short reply ("yes", "approved", "ok"). Any reply starting with "no" or "hold" declines.
  Anything else is not counted, and the bot says so.
- **Guild chat:** Robert's own turn "approve D-12" / "no D-12". MC's text never counts.
- The receipt keeps the channel, the ids, his exact words and the SHA-256 of the decision text. The decision must
  still be waiting, unexpired (24 h) and unchanged. The first answer wins.
- An agent acts only on an `approved` receipt for exactly the words it asked.

## The check-in (every 15 min)

The supervisor reads open PRs and their checks, release workflow runs (`deploy.yml`, `deploy-mc-agent.yml`,
`deploy-iotconnect.yml`) and the newest stop line of each agent session. It does three things:

- **Nudges, with fixed texts, never model-written:**
  - a session waiting on CI, once its PR's checks finish;
  - a session whose turn ended with no stop line, after 20 min.
- **Tells Robert once per item:**
  - errors: failed CI, a failed release, an agent in `error`;
  - `blocked` sessions;
  - questions waiting on him that were not filed as decisions;
  - green PRs waiting for his merge;
  - reviews waiting over an hour;
  - nudged sessions that did not resume.
- **Records every check-in** in `guild.supervision_checkins`. It sends Telegram only when something is new.

## Turn on (once)

1. **Robert makes the bot** (about 3 minutes): Telegram → @BotFather → `/newbot` → name "MiniMoi Approvals". Store
   the token as a SecureString in Parameter Store: `/minimoi/production/telegram_approvals_bot_token`. Then open the
   bot and press Start, so it can message him.
2. **Main release** with this change: the portal path stays off and no supervisor runs yet.
3. **MC tag** `mc-agent-v0.1.15-1`: persona 0.1.15, and the migration in MC's release bundle.
4. **Migrate** on EC2: `sudo /opt/minimoi/scripts/mc_prod_db_migrate.sh rehearse`, then `… apply` (it backs up first).
5. **Mac:** `bash scripts/mc_host/install_supervision_sync.sh`. It prints only the token's SHA-256.
6. **EC2:** `sudo /opt/minimoi/scripts/deploy_mc_container_ec2.sh supervision on <sha256>`. This projects the bot
   token (root 0600, read-only in the supervisor), sets `MINIMOI_SUPERVISION=1` and the hash in `/opt/minimoi/.env`,
   starts the supervisor on the portal's tag and redeploys the portal alone.
7. **Claude Code hook** (Robert, since agents may not change their own settings): merge
   `scripts/agents/claude-stop-hook.settings.json` into `.claude/settings.json` in the shared checkout. AGENTS.md
   gets the section in `scripts/agents/STOP_LINE.md`.

## Check

- `sudo /opt/minimoi/scripts/deploy_mc_container_ec2.sh supervision status`: the container is healthy, and the
  log shows `check-in N` lines 15 minutes apart.
- Ask a test decision from any Claude Code session:
  `python3 scripts/agents/stop.py ask "Test decision: reply yes to prove the loop"`. It arrives on Telegram within
  about a minute. Reply "yes" to that message. `python3 scripts/agents/stop.py wait --key <key>` then exits with the
  receipt.
- The Mac sync log: `~/.minimoi/supervision/sync.log` (silent when there is nothing to carry).

## MC hands work to Claude Code or Codex on the Mac (delegation)

MC proposes delegated research (`minimoi_delegate_research`, agent `claude-code` or `codex`). Robert confirms, and the
job is queued in prod's `guild.mc_delegations`. The Mac runner claims it through
`/internal/mc/v1/delegation-runner`, which is outbound HTTPS from the Mac; prod's database is never exposed. The
runner works in a fresh read-only checkout of `main` and returns the answer as the job's result note in that Guild
conversation.

- Limits: Claude Code is capped at 2 USD per job. Codex runs on Robert's plan, research only, read-only sandbox,
  30 minutes per job (Robert, 26 Oct: "30 minutes and we adjust if needed to be higher":
  `CODEX_DEADLINE_S` in `core/mc_delegation_dispatcher/relay.py`). One job at a time.
- Codex runs with `--ignore-user-config`. The Mac user's configured model needs a newer CLI (26 Oct), and a
  delegated job should carry none of his settings or MCP servers anyway.
- Turn on, after this is on main:
  1. Mac: `bash scripts/mc_host/install_delegation_runner.sh`. It prints the token's SHA-256 and loads
     `ai.minimoi.delegation-runner.prod` from a bundle of `origin/main`.
  2. EC2: `sudo /opt/minimoi/scripts/deploy_mc_container_ec2.sh runner on <sha256>`.
  3. Check that the next check-in lists the runner, then ask MC for a small research job.
- The check-in shows every delegated job: running (agent, minutes), unclaimed after 5 min (no runner), silent
  after 5 min, or ended badly.
- Acceptance test: Robert asks MC to triage the open PRs (#282–#375). MC delegates one read-only review, follows it,
  and brings back close / merge / finish / investigate with one line each. It then proposes each close for his yes.
  Closing a PR needs the issues token to carry Pull requests: write (Robert's choice). Without it, MC proposes a
  comment and label instead.
- Off: `deploy_mc_container_ec2.sh runner off`; on the Mac,
  `launchctl bootout gui/$(id -u)/ai.minimoi.delegation-runner.prod`.

## Off and rollback

`sudo /opt/minimoi/scripts/deploy_mc_container_ec2.sh supervision off`: stops the supervisor, removes the token copy,
clears both keys and redeploys the portal. On the Mac: `launchctl bootout gui/$(id -u)/ai.minimoi.supervision-sync`.
The tables stay; nothing else depends on them.

## Limits (26 Oct)

- MC's own OpenClaw clock is not used. A scheduled OpenClaw turn has no live owner turn, so MiniMoi's tools refuse
  it (`core/mc_tools/service.py`, `_bind`). The supervisor is a plain process; MC carries the intent in the Guild chat.
- A nudge wakes a Claude Code session only if that session left `stop.py wait` running. Otherwise the nudge waits
  in its inbox, and after 40 min Robert is told the session stalled.
- Codex has no Stop hook. It prints the line with `stop.py line --agent codex --session <thread id>`.
