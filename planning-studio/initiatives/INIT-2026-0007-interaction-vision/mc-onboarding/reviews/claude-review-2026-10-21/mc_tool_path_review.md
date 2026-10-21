# MC tool path review (21 Oct)

**Reviewer:** Claude Code, in a fresh session (read-only; no product code changed, nothing deployed or merged).
**Branch:** `claude/mc-tool-path-86de08` at `e316964f` (on top of `claude/dev-journeys-20261021`).
**Scope:**
- `core/mc_tools/` and `services/mc_tools/`;
- the `minimoi-tools` OpenClaw plugin and `MC_CHAT_AGENTS_SECTION.md`;
- the `api.py` hooks `_tool_binder` and `_owner_tool_proposal_command`;
- the deploy handoff's `openclaw.json` edits;
- conformance with `planning-studio/governance/CHANGE_CONTROL_STANDARD_ITIL_v0.1_2026-10-21.md`.

## Verdict

**Do not deploy to dev as written.** Two P1s:

1. **Robert's "yes" isn't an informed approval.** A plain "ok", "sure" or "yes" approves the newest pending proposal, but the platform never shows Robert that proposal's exact text: only MC's retelling of it. Proposals come from model output, and the model reads attacker-writable public GitHub text.
2. **The binding trusts headers.** Any process holding the executor token can act as Robert's live turn. Every agent on Robert's OpenClaw gateway, including `mc-agent` (full tools, sandbox off), runs as the OS user that owns that token.

What holds: proposals and effects go through C06 receipts and C07 grants; the binding dies with the ledger claim; voiced turns get no binding; tool arguments carry no authority. The failures are in *who* can call, and *what* Robert's yes actually saw.

## Tests run

| Suite | Result |
|---|---|
| `tests/mc_tools/run_postgres.sh` (real PostgreSQL 16.2, under `heavy_job.py`) | **41/41 OK**, plus my 6 reproductions (47 run) |
| `node --test tests/mc_tools/test_minimoi_tools_plugin.mjs` | 8/8 pass |
| `tests/mc_tools/test_portal_wiring.py`, `test_listener_and_catalogue.py` | 15 OK |
| Guild regression (`tests/guild tests/mc_lessons tests/mc_tools tests/mc_domain_actions tests/mc_delegation_runner`, under `heavy_job.py`) | **3,055 passed, 119 skipped** |

**Reproductions:** `repro/repro_mc_tools_postgres.py`. Copy it to `tests/mc_tools/test_zz_review_repro.py`, run `tests/mc_tools/run_postgres.sh <pgbin>`, then delete it. All six tests pass, which means each defect reproduced. They ran against an exported copy of the branch, never a checkout.

---

## P1

### P1-1: A plain "ok" approves a proposal whose exact text Robert never saw from the platform
- `core/mc_tools/proposals.py:38-49` (`_YES`), `:127-183` (`_confirm`);
- `minimoi_portal/guild_ui/api.py` `_owner_tool_proposal_command`;
- `MC_CHAT_AGENTS_SECTION.md` ("Tell him what the tool's result says, in your own words").

**What goes wrong.** The exact proposal text (`words` in `service.py`, for example the full public comment) is returned *only to the model* as the tool result. Nothing in the portal shows it to Robert: the model paraphrases it, or doesn't. Robert's next short reply is then taken as approval of whatever is newest:
- the yes words: `yes`, `ok`, `okay`, `sure`, `yep`, `yeah`, `go ahead`, `do it`, `please do`, `confirm`, `approve`;
- the approval covers the newest pending proposal in the conversation, which a new one silently replaces.

The model's input includes **attacker-writable text**:
- issue bodies and comments in the public repository, via `minimoi_github_issues`; anyone can comment;
- guest names and emails, via `minimoi_guest_requests`.

So an outside party can steer MC into proposing something and describing it as something else ("Shall I keep an eye on #69?"). Robert's "ok" then:
- posts attacker-chosen text publicly;
- files a ticket;
- rejects a guest;
- or, with invites on, creates a Meu Português login and emails its password to an address the model supplied.

The posted footer then says "Robert approved this exact text in the Guild conversation", which is false.

**Reproduced:**
- yes, on real PostgreSQL (`test_T2_…`): a model-originated comment proposal, then `parse("ok")`, gives `verified`, and the comment is posted with that footer;
- the platform not rendering the proposal is from reading the code: the proposal `words` go only into the tool result.

**Fix:**
- The platform, not the model, must show every consequential proposal verbatim in the conversation: a platform note, or a card with the exact text, target and effect.
- Approval must name that proposal: a Confirm control on the card, or "Confirm P-3". Not a bare "ok" or "sure".
- Keep plain yes/no only as a reply to a platform-rendered proposal shown in the turn immediately before.
- Never allow more than one consequential proposal per owner turn.
- Treat GitHub and guest text as untrusted input that can't lead to a proposal in the same turn without Robert's own words asking for it.

### P1-2: Any holder of the executor token can impersonate the plugin and act in Robert's live turn, and every gateway agent can read the token
- `services/mc_tools/listener.py:54-72` (trusts the `X-MC-Session` and `X-MC-Agent` headers once the bearer token matches);
- `core/mc_tools/binding.py:68-94`;
- design §3, "What an attacker would need".

**What goes wrong.** The listener authenticates only the token. Session and agent are plain headers, and the session key is derivable by anyone who knows the conversation ID:

```text
agent:mc-chat:openai-user:guild-mc:<sha256("guild:robert:<conv id>")[:32]>
```

So the design's claim that a caller also needs "a run in the exact session" is false; it needs only the token and a conversation ID, during any live owner claim. A caller can loop until Robert's next turn.

On Robert's gateway (`~/.openclaw/openclaw.json`, tool-policy keys only read):
- `mc-agent`, MC's background-job agent, has profile `full` and sandbox `off`;
- `main` has no tool restrictions.

Both run as `vanstedum`, the owner of the 0600 `executor.token`, so either could `cat` the token and `curl` `127.0.0.1:5011`. The same holds for the GitHub issues-write token file on the Mac host. `mc-chat` itself has `read`, `grep` and `find` with sandbox off; whether OpenClaw's `read` is limited to the workspace wasn't verified. If it isn't, the token can also land in the model transcript.

**Reproduced:**
- yes, real PostgreSQL plus the real listener (`test_T1_…`): a non-plugin caller with the token and a computed session key got `done/listed`, including guests' emails, and `needs_confirmation` for a public comment;
- the agent-reads-token step comes from the gateway config. I didn't execute it on the live gateway.

**Fix:**
- Don't let the token be the boundary while exec-capable agents share the gateway's OS user. Run `mc-chat` (and the plugin and its token) in a separate gateway under its own OS user, with no exec agents. Or keep the tools off until per-run `toolBindings` (N1/RC9-F1) exist.
- Mint a random per-turn nonce at dispatch, stored only with the binding. It can't reach the plugin without `toolBindings` today, which is the real blocker; say so in the design.
- Remove the reads of personal data (the guest list) from the tool surface until the binding is real.
- Keep the issues-write token out of any directory a gateway agent can read.

---

## P2

| # | Where | Finding | Reproduced | Fix |
|---|---|---|---|---|
| P2-1 | `proposals.py:129-131` | **A retried yes confirms a newer proposal.** The query is `state='pending' OR (state='confirmed' AND confirm_turn_id=…) ORDER BY created_at DESC LIMIT 1`, the same defect as dev-journeys P1-1. A retried yes after a newer proposal approves the newer one. The tool path requires the turn ledger, which refuses most retries, so P2 here. | Yes (`test_T3_…`: comments posted on #69 and #70 for one yes) | Resolve `confirm_turn_id = turn_key` first. Otherwise take only a pending proposal created before the confirming note. |
| P2-2 | `proposals.py:99-106` (`has_pending`) | **For 30 minutes after any confirmed tool proposal, "ok", "yes", "sure", "no" and "don't" are taken by the platform.** It answers "Nothing is waiting…" and MC never sees the message. These are Robert's most common short replies. | Yes (`test_T4_…`) | Claim only a live `pending` proposal, or `confirm_turn_id = turn_key`. Drop the 30-minute window. |
| P2-3 | `service.py:207-232` and `proposals.py` | **Lesson and tool proposals collide.** A lesson proposed through the tool waits in the lesson ledger and needs "Confirm", but the agents section tells MC that "nothing happens until Robert answers yes". If any tool proposal is pending, "yes" approves *that* (a public comment, say), not the lesson. If none is pending, "yes" goes to the model and the lesson is never kept. Guest Confirm (with its 30-minute window, dev-journeys P2-4) still runs first. | Yes (`test_T5_…`: "yes" posts the earlier comment; lessons `[]`) | Use one proposal register across guest, lesson and tool proposals, with one Confirm. Or never let two families be pending in one conversation; a new proposal supersedes all others. |
| P2-4 | `service.py:149-164` (`_public_text_refusal`) | **The guard against personal data in public text has gaps.** It checks `guest_requests` only, not guest accounts or other people. It skips `_GENERIC_WORDS`, which include **"robert"**, so a guest named Robert is never caught. There is no NFKC or confusable folding. Spacing defeats it. All of these were proposed for public posting: "Rejected Robert's request…" (guest "Robert Ng"), "Rejected Аna's request…" (Cyrillic А), "Rejected B r u n o's request…". | Yes (`test_T6_…`) | Prefer templated public text (refs, change, environment) with no free text, or show the free text verbatim to Robert (P1-1) *and* filter after NFKC, confusable folding and whitespace removal, against guests, accounts and the owner list. Don't exempt real names. |
| P2-5 | Deploy handoff §4, `openclaw.json` | **The `openclaw.json` change is unverified against Robert's real plugins.** It removes `group:plugins` from `mc-chat`'s deny list on a gateway with telegram, whatsapp, zoom-meetings, buzz, brave, xai, ollama and memory-core. The probe proved the effective tool list only on an isolated gateway that loaded `minimoi-tools` alone. Also, `register()` throws when the token file is missing or wrong (`index.mjs:189`); whether that stops the whole gateway, and so Telegram and WhatsApp, wasn't verified. | Config read; not run | Rerun the probe with the live plugin *entries* (no credentials) and record `mc-chat`'s full effective tool list. Make `register` log and register nothing instead of throwing. Restart the gateway with the token deliberately missing on a copy, to prove the other plugins stay up. |
| P2-6 | `service.py`, `proposals.py`, `issue_text`; ITIL standard §2, §4, §5 | **Not conformant with the change-control standard.** On `MINIMOI_MC_TOOLS_ENV=prod` (accepted with no extra gate):<br>• a guest reject (production **data**) and an invite (production **access**: account plus credential) run on a chat yes with a receipt, but no change record or ticket is linked;<br>• the data-repair ticket asks "Claude Code or Codex to {change}" production records. It is a work request with no risk, steps, verification, rollback or approver: not an RFC. Nothing makes the eventual repair go through change control. | Code reading | Refuse consequential kinds on prod unless each is a **standard change**: a runbook Robert pre-approved, its ID configured, and each use linked to a ticket. Make the data-repair ticket an **RFC template** (§4 fields, receipt operation IDs), executed only through an approved RFC. Dev may stay as is. |

---

## P3

| # | Where | Finding | Fix |
|---|---|---|---|
| P3-1 | `mc_tools_wiring.py:192` | The listener binds `0.0.0.0`. That's fine inside Docker with `127.0.0.1:5011` published, but if the portal runs on the host (the launchd portal, disabled since 27 Sep) it would be on the LAN. | Use a configurable bind address, defaulting to `127.0.0.1` outside a container. |
| P3-2 | Branch base | The branch is 6 commits behind the `claude/dev-journeys-20261021` tip (operating rules, voicing wording, the guest reject through `reject_guild_in`). A two-dot diff shows those as removals. | Merge the tip and re-run the tool-path suites before dev. |
| P3-3 | `service.py:32`, `_delegate_research` | `codex` is offered, but Codex delegations are refused at claim and end `not_picked_up` after 30 minutes (dev-journeys P3-8). | Offer only actors with a budget enforcer. |
| P3-4 | `proposals.py:273-291` | An invite reopens a *rejected* guest request and overwrites its name, reason and domain. | Create a new request row for owner invites. Don't rewrite the guest's own record. |
| P3-5 | Design §3 | The residual-risk text understates exposure: "anyone who can read it and start an `mc-chat` run is already the owner's own operator". | Restate it after P1-2 is fixed. |

## What holds up (checked)

- **The binding requires the exact live claim:** same principal, conversation, note and generation, with an unexpired lease, under `FOR SHARE`. Finished, lapsed or reclaimed turns refuse (the branch's tests, re-run).
- **Voiced turns record no binding**, through `_tool_binder` (dev-journeys P2-1 respected).
- **Strict argument schemas** on both sides. Arguments never carry identity or authority. Results are bounded, and the token is checked out of them.
- **The owner's own note is the research brief**, never model text; the brief's author is checked.
- **Invite passwords** never reach a reply or the model.

## Test gaps

1. A non-plugin caller with the token (P1-2).
2. A proposal not shown verbatim by the platform, then "ok" (P1-1).
3. A retried yes after a newer proposal (P2-1).
4. "ok" and "no" after a completed proposal (P2-2).
5. Pending lesson and tool proposals together (P2-3).
6. Confusable, spaced and generic-word names (P2-4).
7. The live-plugin tool list, and gateway behaviour on a bad token file (P2-5).
8. Prod refusal of consequential kinds without a standard-change ID (P2-6).
