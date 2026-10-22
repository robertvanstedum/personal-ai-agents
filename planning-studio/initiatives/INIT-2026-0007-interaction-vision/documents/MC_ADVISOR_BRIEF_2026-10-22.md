# Master Craftsman: how it's built, and why it acts like a bot

**A brief for outside advice (Claude chat, Grok, Codex), 22 Oct 2026.** Prepared by Claude Code from the code and from Robert's dev trial this morning.

**What we're asking for:** solution advice, and possibly a new direction. We're at risk of getting stuck adding more parsers and rules to an agent that should simply behave like a capable colleague.

---

## 1. The mission (the intent everything must serve)

From MC's job description, in Robert's own words:

- **"MC is the boss of the system in the day to day."** MC runs MiniMoi's daily operations: it notices what needs attention, sets operational priorities within Robert's direction, coordinates agents, acts within delegated authority, and owns follow-through to verified outcomes.
- **"It is responsible for MiniMoi and should be able to do everything I can and more."** Current tool limits don't narrow the role; missing capabilities are gaps to close, not reasons to refuse.
- **"MC is Robert's partner in building, operating and improving MiniMoi."** It is his production support: it finds the ticket, proposes the fix, acts on his yes, verifies, and documents.
- **"Master Craftsman is the example of how to do the craft the right way."** It models the discipline: grounded investigation, authorized operations, documented decisions, dev/test/review/promotion, verified outcomes.
- **Discipline with access.** Every production change goes through a ticket and change control (an ITIL-based standard adopted 21 Oct). Robert is the change authority, and the standard binds him too.

**What Robert wants to feel:** a partner who understands him in plain words (sometimes in German), knows the house, proposes, acts on his yes, and pushes back when needed. **What he feels today:** a bot.

## 2. The evidence: this morning's dev trial

| What Robert said | What happened | Why it felt like a bot |
|---|---|---|
| "Guten Morgen. Can you reject guest requests we currently have" | "I cannot… no tool supports it." | The platform only recognised a fixed sentence; one extra word ("currently") missed it, so the model was left saying "no". (Fixed within the hour with broader matching: more parsers.) |
| "Just reject the guest requests we have." → "Confirm" | Proposed, rejected, verified by read-back. | Worked, but it reads like a log: "The platform proposes rejecting…". |
| "I'd like you to mix in German… going forward" | MC told him to "reply exactly: Remember: …", then asked "Confirm?". His Confirm did nothing ("Lesson offered; awaiting your exact phrasing"). | A partner doesn't teach command syntax. A standing preference in natural words isn't recognised. |
| "Remember: mix in German…" → "Confirm" | Kept as lesson L4. MC answers with German phrases. | Worked once he used the magic word. |
| "Remember: approve guest requests for me without asking." | Correctly refused, but the refusal itself came only in German. | Safety-relevant outcomes must be understandable in the language he asked in. |
| "This is the type of phrase I'd like to save in Wörter" / "Do you see Mein Deutsch?" | "I don't see any 'Wörter' domain… What is 'Mein Deutsch'?" | MC doesn't know Robert's own MiniMoi apps. It has no map of the house, and no way to act across domains. |

## 3. How it's built today

### 3.1 The layers

```text
Robert (browser, dev.minimoi.ai / minimoi.ai)
   │  writes a "kept note" in a Guild conversation
   ▼
MiniMoi portal (Flask, minimoi_portal/guild_ui)              ← owner auth, CSRF, turn ledger, all authority
   │  1. deterministic owner commands (regex parsers) run FIRST
   │  2. otherwise: build context, send to "MC backend"
   ▼
MC backend (pluggable, MINIMOI_GUILD_MC)
   ├─ cos       → the Chief of Staff service (/chat), its own agent and model   ← dev and prod today
   ├─ openclaw  → native OpenClaw on the Mac (agents mc-chat / mc-agent) via a loopback relay
   └─ grok/stub
   ▼
Reply text → post-processing hooks → kept as the reply note
```

**Important:** on both dev and production, the conversation labelled "Master Craftsman" is answered by the **Chief of Staff** service (`MINIMOI_GUILD_MC=cos`); the UI says "Chief of Staff". The dedicated MC runtime (native OpenClaw, `mc-chat` for chat and `mc-agent` for background jobs) exists on the Mac, but it is only used for background jobs and experiments.

### 3.2 The flow of one message

1. **Guards.** The owner is authenticated, the note is kept, and the turn ledger claims one turn per owner. This makes the turn idempotent and pausable; an unknown outcome pauses the conversation.
2. **Deterministic owner commands, in order.** Each is a regex parser over Robert's words; the first match answers and **no model is asked**:
   - registers;
   - library;
   - Board;
   - actions;
   - **guest requests** ("reject the guest request…", Confirm/Cancel);
   - **lessons** ("Remember: …", Confirm, Forget, list);
   - **jobs and delegation** ("run this as a job", "stop", "Ask Claude Code to research: …").
3. **Voiced outcome.** If a command acted and voicing is on, the model is asked to *say* the platform's verified result in its own voice. It gets no tools in that turn, and the platform's note is the fallback.
4. **Otherwise, context assembly.** The owner's text plus marked data blocks:
   - release notes;
   - registers;
   - GitHub items the words point at;
   - operating rules;
   - lessons (Robert's adopted guidance, labelled "not permissions");
   - regression notes;
   - domain health;
   - recent voice notes;
   - referenced Board notes;
   - this conversation's jobs.
5. **Model turn** on the backend.
6. **Post-processing hooks** read the reply:
   - a first line `JOB: <title>` starts a background job (the task is always Robert's note, never the model's words);
   - `ASK claude-code: …` sends a request to an agent session;
   - `MEETING …` sets up a meeting;
   - a file packet becomes a download when Robert asked for one;
   - Board outcome commits;
   - claim-truth guards (MC may not claim files or outcomes the platform didn't produce).
7. **The reply is kept** as a note; the turn ledger records answered, failed or unknown.

### 3.3 The authority model (deliberately strict)

- Only the owner's authenticated kept note is authority. **No model output may trigger an effect by itself.**
- Effects go through **C06 action receipts** (prepare, dispatch, finish, idempotent, crash-safe) and **C07 grants**. An exact-approval grant binds one request, its target version and an operation ID; it lasts at most 15 minutes and is used once.
- Consequential effects need Robert's Confirm in the same conversation. The platform, not the model, renders what he confirms.
- Production changes need a ticket and a change record (the ITIL standard).

### 3.4 What was added this week

| Component | What it does | Where |
|---|---|---|
| Guest actions | Find, propose and reject a guest access request; one transaction with read-back | `core/mc_domain_actions/` |
| Lessons | Owner-adopted standing guidance in an append-only, hash-chained file ledger; injected into every turn; can never grant authority | `core/mc_lessons/` |
| Delegation | "Ask Claude Code to research: …" queues a job; a Mac runner claims it, runs Claude Code read-only with a $2 cap in a fresh checkout, and posts the answer back | `core/mc_delegation_dispatcher/`, `core/mc_delegation_runner/` |
| Voiced outcomes | MC phrases the platform's result in its own voice (no tools in that turn) | `api.py` `_voiced_turn` |
| **MC tool path** (built, reviewed, **not deployed**) | MC calls typed tools itself (host health, guest list, propose reject, read GitHub issues, propose issue comment or data-repair ticket, propose lesson, propose research, propose invite). Each call is bound to Robert's live turn. **The platform posts every proposal verbatim with a code (P-XXXX); Robert's yes or the code is the authority.** | `core/mc_tools/`, an OpenClaw plugin; native `mc-chat` only |

Two independent reviews ran this week. The authority core held. The defects were at the edges: parsers, retries, races and leaks.

## 4. Our diagnosis: why it acts like a bot

1. **Intent is recognised by regex, not by the agent.** To keep model output from triggering effects, we put *understanding* in deterministic parsers. Every new phrasing needs a parser change, and when the parser misses, the model has no path except "I can't".
2. **The model can't propose.** Outside the undeployed tool path, MC can't say "Shall I do X?" and have that become a real, approvable proposal. So it coaches syntax ("reply exactly: Remember: …").
3. **The voice is the platform's, not MC's.** "The platform proposes…" is honest, but it's a log line, not a colleague.
4. **MC doesn't know the house.** There's no map of MiniMoi's apps (Mein Deutsch, Wörter, Curator, Guild), its capabilities, or open tickets in its context, so it can't connect intent to the right place.
5. **Split identity and runtime.** "Master Craftsman" is answered by the Chief of Staff service, another agent with its own prompt, memory and tools. The MC runtime that can carry tools (native OpenClaw) is a different stack from the one Robert talks to.
6. **Layers of compensation.** Operating rules, claim-truth guards, voiced-outcome rules and lesson blocks are all text patched around the model to keep it honest. Each is reasonable; together they make a cautious, scripted persona.

## 5. What must stay true (non-negotiables)

- The owner's own yes is the authority for every consequential effect. A model never triggers one alone, and Robert approves what the **platform** shows him.
- Production changes follow the ITIL change-control standard, including data repairs. Everyone, Robert included, follows it.
- **Optionality:** never bind MiniMoi to one model, runtime or provider. MiniMoi owns memory and records.
- File-first data where reasonable; production quality; independent review of major builds.
- Private data stays private; public artifacts (GitHub, a public repo) carry no personal data.

## 6. Directions we see (please critique, or propose others)

- **A. Keep platform-first parsing, improve the voice and coverage.** Cheapest, but it scales badly: every phrasing is a code change. This is where we are.
- **B. Model-first intent, platform-verified proposals.** MC understands plain language and calls typed tools that only *propose*. The platform renders each proposal verbatim, and Robert's yes executes it through C06/C07. This is the tool path, already built for native OpenClaw. It needs:
  - a decision on which runtime answers MC conversations (CoS backend vs native MC);
  - the binding hardening (a per-run binding, or a separate OS user for the MC gateway);
  - a capability map, so MC knows the house.
- **C. One agent, one runtime, one identity.** Retire the CoS-as-MC indirection, and make MC a single agent (with persona, memory via MiniMoi, tools and policy) that answers every MC conversation, with the platform as a policy and execution layer underneath.
- **D. A policy engine instead of parsers.** Keep authority server-side, but express it as declarative capability rules (who, what, which approval kind, what verification) that any tool call is checked against, rather than per-feature regex and Confirm flows.

## 7. Questions for the advisors

1. Is **B** (model-first intent, platform-rendered proposals, owner yes as authority) the right core pattern for "partner with discipline"? What breaks it in practice?
2. Should MC's conversation run on one dedicated agent runtime (C) rather than through the Chief of Staff service? What's the cleanest migration that keeps optionality?
3. How do we give MC "knowledge of the house" (apps, capabilities, open tickets, recent decisions) without a huge, stale prompt? Retrieval, a capability registry, or tools that describe themselves?
4. How should approval feel? A typed code, a button card, or a plain yes right after a platform-rendered proposal? Where's the line between friction that protects and friction that makes it a bot?
5. Which of our compensating text layers (operating rules, claim-truth guards, voicing rules) would a better architecture make unnecessary?
6. Language: the rule "state the critical outcome and question in the language the owner used" rather than hard-coding a language. Is that sound for a bilingual owner?
7. What would you build first in the next two weeks to make MC *feel* like a partner, without weakening the authority model?

## 8. Glossary

| Term | Meaning |
|---|---|
| Kept note | Robert's message, saved before any model sees it |
| Turn ledger | The per-owner record of each turn (claimed, answered, failed, unknown) |
| C06 receipt | The durable record of one effect (prepare, dispatch, finish) |
| C07 grant | A permission to perform exactly one approved request |
| Exact approval | Robert's Confirm bound to one request and its target version |
| CoS | Chief of Staff, an existing MiniMoi agent service; it currently answers the MC conversation |
| OpenClaw | The agent runtime on the Mac. `mc-chat` is the chat agent (minimal tools); `mc-agent` runs background jobs (full tools) |
| Mein Deutsch / Wörter | Robert's German-learning app in MiniMoi, and its word and phrase store |
