# MC tool path: binding a model tool call to the owner's turn

**Status:** design, built on `claude/mc-tool-path` (21 Oct 2026). Claude Code, at Robert's request ("It should be like anything else").
**Deploy handoff:** `docs/design/mc_tool_path_dev_deploy_handoff_2026-10-21.md`. Both belong in `planning-studio/.../mc-onboarding/handoffs/` too; this worktree session cannot write the shared checkout, so the dev operator copies them.

## 1. The question

Master Craftsman (MC) should decide from Robert's plain speech and call tools itself. The open S0 question is how a model tool call reaches MiniMoi **bound to the owner's authenticated turn**, never to identity the model supplies.

## 2. What the runtime gives us (checked, not assumed)

Dev MC is the native OpenClaw **2026.9.4** on the Mac (`mc-chat` agent), reached by the portal through the loopback relay (`services/mc_operations`). Read from the installed package:

| Fact | Source |
|---|---|
| The portal's `user` field becomes the session key `agent:<agentId>:openai-user:<user>` | `openai-http-*.mjs:480–540`, `http-utils-*.mjs:144` (`resolveSessionKey`), `grammar-*.mjs:20` |
| The portal sets `user` = `guild-mc:` + 32 hex of SHA-256(session conversation id) | `minimoi_portal/guild_ui/mc/openclaw.py` `_user` |
| A plugin tool factory receives `ctx.sessionKey` and `ctx.agentId` from the host, outside model arguments | `OpenClawPluginToolContext`, `agent-harness-runtime-*.d.ts:3610` |
| `ctx.toolBindings` exists, but `chat.send` accepts it only from a paired browser-copilot client (N1), and the factory context has no `runId` (RC9-F1) | Codex status, `CODEX_BUILD_STATUS.md` |

So the runtime cannot carry a per-turn secret into the tool without the gated `toolBindings` path. It **does** carry, unforgeably by the model, the session key the portal itself chose.

**Proved on the real runtime** (`tests/mc_tools/openclaw_native_tool_probe.mjs`, installed 2026.9.4, isolated gateway, fake provider): a tool call from `mc-chat` reaches MiniMoi with `X-MC-Session: agent:mc-chat:openai-user:guild-mc:<32 hex>` and `X-MC-Agent: mc-chat`; `main` is offered none of the tools.

## 3. Mechanism: the live turn-ledger claim is the binding

The narrowest proven mechanism uses two things MiniMoi already trusts, and adds one row.

1. **The portal issues a per-turn binding at dispatch.** When an owner turn that the durable turn ledger (#346, `MINIMOI_MC_TURN_LEDGER=1`) has claimed is about to be sent to MC, the portal writes `guild.mc_tool_turns`: ledger id, claim generation, principal, conversation, the owner's kept note id, and the `user` value it is sending. This happens before the runtime can run any tool.
2. **The plugin presents only host context.** The tool plugin sends `ctx.sessionKey` and `ctx.agentId` (host fields) with an executor credential that it reads from a private file. Model arguments are validated against a strict per-tool schema and never contain identity, ids of turns or authority.
3. **The platform re-checks the turn ledger on every call.** The tool service parses the session key, requires the agent to be an allowed MC agent, and finds the binding whose ledger entry is **still `claimed`, at the same claim generation, with its lease unexpired, for the same principal, conversation and note**. Exactly one row must match. That ledger entry *is* the owner's authenticated turn (owner session, CSRF, kept owner note, one running turn per owner). From it the service builds the `ActionContext`.

**Closing is free.** When the turn finishes, fails, is stopped or its lease lapses, the ledger entry leaves `claimed`. Every later call (a late callback, a retained tool closure, a run in another session) finds no live binding and is refused before any adapter runs. No separate close step can be forgotten.

**What an attacker would need.** To act, a caller needs the executor credential *and* a run in the exact session that owns a live owner claim, during that claim. The model can do neither: it cannot choose the session key or the agent, and it cannot extend a claim.

### Residual risks, stated plainly

- **Same-session runs during a live claim.** Any run in that session while the owner's turn is claimed is treated as that turn. Only the relay starts runs in `mc-chat` sessions, it holds one turn in flight, and `mc-chat` denies `sessions`, `subagents` and messaging tools. This is weaker than a per-run binding (Codex's `BindingStore` + `toolBindings`), which remains the target once N1/RC9-F1 are solved; the service is written so that swap changes only binding resolution.
- **The executor credential lives on the runtime host.** It is a file readable only by the runtime user, and the service keeps only its SHA-256. Anyone who can read it and start an `mc-chat` run is already the owner's own operator.
- **Plain HTTP on loopback.** The plugin refuses any endpoint that is not `https:` or exactly `http://127.0.0.1:<port>`. The listener is a separate port, published only to Mac loopback, and is not behind the Cloudflare tunnel (which routes only `localhost:5001`).

## 4. Authority: references and reasons only, effects through C06/C07

| Tool (model name) | Capability | What the model may supply | Effect |
|---|---|---|---|
| `minimoi_host_health` | `host.health` (read) | nothing | none; the plugin measures the runtime host |
| `minimoi_guest_requests` | `guest.list` (read) | nothing | none |
| `minimoi_guest_request_reject` | `guest.propose_reject` → `guest.reject_request` | `ref` from the list, a short reason | proposal; reject only after the owner's **Confirm** |
| `minimoi_lesson_propose` | `lessons.propose` | the lesson text | proposal; adoption only after **Confirm** |
| `minimoi_delegate_research` | `delegate.propose_research` → `delegate.start_research` | agent, a short title | proposal; the job's brief is the owner's own kept note, never model text |
| `minimoi_access_invite` | `access.propose_invite` → `access.invite_user` | name, email, days; app fixed to Meu Português | proposal; account only after **Confirm** |

- **Proposals** are effects too, so each one runs through C06 receipts under a **turn grant** that the tool service issues for exactly that request (one operation, owner turn id = the ledger turn, expiry ≤ the claim's lease). It is zero-spend and needs no exact approval: the owner's live turn authorizes MC to *ask*.
- **Consequential effects** need a C07 **exact-approval grant**, issued only by the platform's owner-command path when the owner types Confirm in the same conversation (the existing guest and lesson flows, extended to delegation and invites). The model has no confirm tool. A proposal lasts 10 minutes and binds its exact request.
- **Delegation spend.** C07 refuses paid capabilities. `delegate.start_research` authorizes *queueing* a capped request; spend admission stays with the runner, which refuses any actor without a budget-binding adapter and enforces the job's cap (`--max-budget-usd`, $2). This is flagged for Codex's review.
- **Secrets never reach the model.** An invite's temporary password goes only to the invitee's approval email (and, as on the existing Operate grant path, the owner's Telegram notice if email fails). Tool results are bounded JSON with a plain sentence for MC to voice.

## 5. Envelope (published interface)

A tool handler receives a `ToolCall`: the validated arguments, the trusted `observation` (plugin-measured, `host.health` only), and a `TurnBinding` (principal, environment, conversation, owner note id, ledger id, generation, lease end) with its `ActionContext`. It returns `{status, code, message, data}`, with status one of `done`, `needs_confirmation`, `needs_clarification`, `refused`, `unknown`. Registration is a static catalogue (`core/mc_tools/catalogue.py`): a tool exists only if it is listed there **and** in the plugin's operator configuration.

## 6. Not in this change

- Production. Dev only, behind `MINIMOI_MC_TOOLS=1`.
- Codex's run-ID binding (`BindingStore`, `/internal/mc/v1/actions`), which stays as is.
- Removing the owner-command phrases: they still answer first, as a bridge.
