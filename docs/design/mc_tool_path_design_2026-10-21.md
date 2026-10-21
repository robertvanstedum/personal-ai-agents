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

**What the binding does and does not prove (review P1-2, 21 Oct).** The model cannot choose the session key or the agent, and it cannot extend a claim, so model output alone never acts. But the listener authenticates only the executor credential: the session key and agent arrive as headers, and the session key can be derived from a conversation id. **Any process that can read the credential can therefore call as Robert's live turn while one is claimed.** On dev today that includes every agent of Robert's OpenClaw gateway that can run commands (`mc-agent` and `main` run as the same OS user that owns the file). A per-turn secret would close this, but nothing can carry one to the plugin until OpenClaw accepts `toolBindings` from MiniMoi's backend (N1, RC9-F1).

### What limits it now

- **Nothing personal reaches a caller.** The guest list returns refs, app and date; the platform shows Robert who a request is from only when MC proposes acting on it.
- **A forged call can only offer, visibly.** Every proposal is shown by the platform, word for word, with its code (§4). Unless the operator states `MINIMOI_MC_TOOLS_RUNTIME_ISOLATED=1`, every proposal needs Robert to name its code, so a forged one can never ride on his next plain "ok".
- **The real boundary is deployment.** Run `mc-chat`, the plugin and its credential in a gateway under its own OS user, with no command-running agent, and the issues-write token out of any directory such an agent can read. Only then set `MINIMOI_MC_TOOLS_RUNTIME_ISOLATED=1`. Until then the tools stay useful, but the credential is not a security boundary.

### Other residual risks

- **Same-session runs during a live claim.** Any run in that session while the owner's turn is claimed is treated as that turn. Only the relay starts runs in `mc-chat` sessions, it holds one turn in flight, and `mc-chat` denies `sessions`, `subagents` and messaging tools. A per-run binding (Codex's `BindingStore` + `toolBindings`) remains the target; the service is written so that swap changes only binding resolution.
- **Plain HTTP on loopback.** The plugin refuses any endpoint that is not `https:` or exactly `http://127.0.0.1:<port>`. The listener is a separate port, published only to Mac loopback, and is not behind the Cloudflare tunnel (which routes only `localhost:5001`).

## 4. Authority: references and reasons only, effects through C06/C07

| Tool (model name) | Capability | What the model may supply | Effect |
|---|---|---|---|
| `minimoi_host_health` | `host.health` (read) | nothing | none; the plugin measures the runtime host |
| `minimoi_guest_requests` | `guest.list` (read) | nothing | none |
| `minimoi_guest_request_reject` | `guest.propose_reject` → `guest.reject_request` | `ref` from the list | proposal; after the owner's **yes**, the guest action path rejects it and verifies by read-back |
| `minimoi_github_issues` | `github.issues.read` (read) | an issue number or a search query | none; public read of one fixed repository, untrusted data |
| `minimoi_issue_comment` | `issue.propose_comment` → `issue.comment` | issue number, a short summary, refs | proposal showing the exact public text; posted only after the owner's **yes**; names and emails refused |
| `minimoi_data_repair_ticket` | `issue.propose_data_repair` → `issue.create_data_repair` | guest-request refs (`guild:N`) and a change (reject, grant, remove) | proposal; a public GitHub issue naming refs only, opened only after the owner's **yes** |
| `minimoi_lesson_propose` | `lessons.propose` | the lesson text | proposal; adoption only after **Confirm** |
| `minimoi_delegate_research` | `delegate.propose_research` → `delegate.start_research` | agent, a short title | proposal; the job's brief is the owner's own kept note, never model text |
| `minimoi_access_invite` | `access.propose_invite` → `access.invite_user` | name, email, days; app fixed to Meu Português | proposal; account only after **Confirm** |

- **Proposals** are effects too, so each one runs through C06 receipts under a **turn grant** that the tool service issues for exactly that request (one operation, owner turn id = the ledger turn, expiry ≤ the claim's lease). It is zero-spend and needs no exact approval: the owner's live turn authorizes MC to *ask*.
- **Consequential effects** need a C07 **exact-approval grant**, issued only by the platform's owner-command path from Robert's own reply. The model has no confirm tool. Since the review (P1-1, P2-1..P2-3):
  - **The platform shows every proposal**, word for word, as its own note in the conversation, with a code (P-XXXX). MC only learns that it was shown; Robert approves the platform's text, never MC's retelling.
  - **A named yes** ("yes P-7K3M") always approves that proposal. **A plain yes** approves only when it is Robert's very next message after the proposing turn, MC has read no GitHub text anywhere in this conversation (its session remembers what it read, so the taint never expires there; re-review R-1), and the runtime is stated isolated (§3). Until it is, every card also says MiniMoi can't yet prove the card came from MC (R-2). A retried yes resolves to what that same note confirmed.
  - **One register, one proposal per turn**: lessons, guest rejects, comments, change records, research and invites all wait in `mc_tool_proposals`; a new proposal withdraws any older one, including a guest or lesson proposal left by the phrase commands. A proposal lasts 10 minutes and binds its exact request.
  - **Production** (change-control standard §2, §5): a data change (guest reject) or an access change (invite) is offered only as a standard change Robert pre-approved, named on the card, with each use logged on its ticket. The data-repair ticket is a draft change record with the §4 fields, which authorizes nothing.
- **Robert's direction (21 Oct): "I need MC to be my production support. Discipline with access."** MC rejects guest requests on his yes, reads issues for context (for example #69, the lost guest approval path), and documents workarounds on issues on his yes. Anything public names records by ref only; the service refuses text containing an email address or the name of anyone who asked for access.
- **Voiced outcomes do not get tools.** When MC voices a platform outcome (the reply to Robert's yes), no binding is recorded, so it can only speak (review P2-1; this composes with the parent's `run_mc_turn(..., voice_only=True)`).
- **Delegation spend.** C07 refuses paid capabilities. `delegate.start_research` authorizes *queueing* a capped request; spend admission stays with the runner, which refuses any actor without a budget-binding adapter and enforces the job's cap (`--max-budget-usd`, $2). This is flagged for Codex's review.
- **Secrets never reach the model.** An invite's temporary password goes only to the invitee's approval email (and, as on the existing Operate grant path, the owner's Telegram notice if email fails). Tool results are bounded JSON with a plain sentence for MC to voice.

## 5. Envelope (published interface)

A tool handler receives a `ToolCall`: the validated arguments, the trusted `observation` (plugin-measured, `host.health` only), and a `TurnBinding` (principal, environment, conversation, owner note id, ledger id, generation, lease end) with its `ActionContext`. It returns `{status, code, message, data}`, with status one of `done`, `needs_confirmation`, `needs_clarification`, `refused`, `unknown`. Registration is a static catalogue (`core/mc_tools/catalogue.py`): a tool exists only if it is listed there **and** in the plugin's operator configuration.

## 6. Approving a guest: designed, not shipped

Approval creates an account and a credential, so it needs more than a reject. Proposed shape, for review before any code:

1. **Offer.** `minimoi_guest_request_approve` (capability `guest.propose_grant`) proposes one open request by ref with a duration (1–30 days). Robert sees who, which app and for how long, privately in the conversation.
2. **Approval.** His yes issues an exact grant for `guest.grant_request` bound to the request's target version (still open, same `requested_at`).
3. **Effect.** The receipt is dispatched and committed, then `minimoi_portal.guest_requests.grant` runs: the hardened account state machine (intent before mutation, credential generation, G-F3 reconcile). Verified means a read-back shows the request `granted` and the account active with a must-change password.
4. **Credential delivery** is a separate notification after the verified effect: the approval email carries the one-time password; `confirm_delivery` records the generation. If email fails, the password reaches only Robert's private Telegram notice (as on the Operate path), never the conversation, the model or a log. Repeat delivery uses `reissue_credential`, never a second grant.
5. **Uncertainty** at any step ends `unknown` with no retry; the grant intent is the reconciliation record.

Review questions: whether Telegram is an acceptable fallback channel; whether the delivery should be a durable outbox (today it is send-then-confirm in one process); and whether approvals need a daily cap. The invite tool already uses steps 3–4 for owner-initiated logins.

## 7. Not in this change

- Production. Dev only, behind `MINIMOI_MC_TOOLS=1`.
- Codex's run-ID binding (`BindingStore`, `/internal/mc/v1/actions`), which stays as is.
- Removing the owner-command phrases: they still answer first, as a bridge.
