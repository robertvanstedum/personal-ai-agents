# Personal OpenClaw response — Claude Chat review of MC-OC-001 v0.3

**Reviewer:** Mini-moi, personal OpenClaw (agent=main, OpenClaw 2026.9.4, anthropic/claude-fable-5-1)
**Date:** 2026-10-24
**Reviewed:** `03_reviews/CLAUDE_CHAT_REVIEW_MC_OC_001_v0.3_2026-10-24.md` (R1–R7, 8-row decision list)
**Checked against:** spec v0.3 (SHA-256 `36d64090…` re-fetched from GitHub today, matches), Claude Code technical review (`docs/reviews/MC_OC_001_TECHNICAL_REVIEW_2026-10-24.md`), my own v0.3 review (F1–F6), live `~/.openclaw/openclaw.json` mc-chat/mc-agent entries, `personal-ai-agents/.gitignore`.
**Nothing installed, changed or restarted.** Config read only.

---

## Verdict

**Agree with the Claude Chat review. Forward it to Grok with the four amendments below.** R1 (Slice 0) is the most useful thing any of the three reviews has said: it names the work Robert will actually feel next week and separates it from the boundary problem. R2's public-clone simplification is correct and I verified its premise. The consolidated 8-row decision list should replace the three separate lists; it supersedes my D1–D6.

Where I push back is on how independent Slice 0 really is. Three of its items quietly depend on decisions the list places later.

---

## Amendments

### A1 — Slice 0 is two slices, not one. Items 1–2 are config/text; item 3 is a MiniMoi build.

- **Web on** (item 1) and **AGENTS dead text out** (item 2) are an `openclaw.json` edit plus a text edit. Hours, Claude Code can do both in one pass, A04 + persona diff verify them.
- **Record-retrieval tool** (item 3) is new MiniMoi server code: a `minimoi_*` tool over the turn log/Records, with scope rules (MC's own conversations + Guild decisions only) and negative tests so CoS can't see MC's and vice-versa. That is days, it needs Codex's answer to my F4 question (does Records & Rooms already expose a read tool?), and it carries A10's gate.

Recommend: **Slice 0a** = items 1–2, ships first. **Slice 0b** = retrieval tool, ships when ready. Otherwise the fast slice waits on the slow item and Robert still feels nothing.

While doing 0a, also **prune `dir_list`, `grep`, `find` from mc-chat's `alsoAllow`** — I confirmed they are still in the live config and Codex already established they are not core catalogue IDs. A04 should assert that only recognised names remain.

### A2 — Slice 0a is a persona change, so decision #5 (persona location) is a Slice 0 prerequisite, not a later item.

Removing dead routing from AGENTS.md *is* a persona release under §6.3. If Slice 0a edits `~/.openclaw/workspace-mc-chat/AGENTS.md` in place, it repeats what I did this morning — an out-of-process edit that Codex then had to baseline after the fact (057b9cc2). The spec exists partly to stop that.

So either: (a) decision #5 is taken now and Slice 0a becomes the **first in-process persona release** (`releases/0.1/`, manifest, hashes, A09 refresh check) — small overhead, large discipline win — or (b) Slice 0a is explicitly labelled a "baseline patch" with its own hash record and the release process starts at Slice 1. I recommend (a). Either way, say which.

### A3 — R2 verified, with two consequences Claude Chat didn't draw.

Verified: `_working/` and `.env` are gitignored (`.gitignore:145`, `:17`); zero `_working` files are tracked; the remote is the public `robertvanstedum/personal-ai-agents`. A fresh clone cannot contain them. The worktree risk is also real: `codex/mc-live-delivery` carries commit 057b9cc2 with private observed runtime files that are deliberately not pushed — a worktree of Robert's checkout would expose them, a clone would not. **R2 stands.**

Two consequences:

1. **Persona releases would have to be pushed to the public remote before MC can load them from the clone.** SOUL/AGENTS for MC are probably fine as public files, but the package currently treats the native workspace files as private evidence ("not pushed in the public spec PR"). Decide explicitly: persona releases are public artifacts, or the native projection comes from somewhere other than the clone.
2. **Remote repository investigation (A02) is partly achievable in Slice 0a.** Because the repo is public, `web_fetch` against `raw.githubusercontent.com`/the GitHub UI gives MC read access to every *pushed* revision with no filesystem boundary decision at all. That is exactly the "Mac-independent lookup" the owner requirement in §10 asks for. Slice 0a should claim A02-remote (pushed content only) and leave local-checkout investigation to Slice 1. Unpushed work stays correctly reported as unavailable.

### A4 — R3 (model first) is right, but it is a shared-gateway change with a shared quota.

mc-chat and mc-agent are both on `anthropic/claude-sonnet-4-6` on **my** gateway. Switching to `claude-opus-5` is an `agents.entries` edit here, under F1: `agents.entries` is hot-reload class, so it should not need a restart, but it must be done as an announced dev change, and the one-day observation window must not overlap my own model/auth experiments or the comparison is confounded twice over. Also: the subscription auth (`anthropic:manual`) is one subscription. Opus-5 for MC and Fable for me draw the same rate limit; if MC's day of ordinary use hits throttling, that is a host artefact, not a model result. Record it.

---

## Minor

- **R6 (log queries/URLs):** partly exists already. mc-chat's conversation and tool-call history is persisted on this host in `~/.openclaw/agents/mc-chat/agent/openclaw-agent.sqlite` (no per-session JSONL directory exists for mc-chat, unlike `main`), and is readable via the Control UI session catalog here. What is missing is MiniMoi-side visibility; a cheap operator view over those transcripts satisfies R6 for Slice 0a without new instrumentation.
- **R5:** agree; one sentence in §11 pointing to the build-plan package. `MC_SOUL_SUGGESTED_v0.1.md` is at the package root (private) for anyone reviewing locally.
- **R7 (Career Narrative Kit):** cannot verify — it exists in Claude Chat's session, not in this package or the repo. Reasonable first CoS use; note CoS is on OpenClaw 2026.7.1 in its container, so the Slice 0 capability set must be re-verified there (my F5). Needs its own spec before it is a commitment.
- **Decision #4:** F2 additionally requires carving `sessions_spawn` out of mc-chat's `group:sessions` deny. One more thing the one-session test must do; not a blocker.

---

## Consolidated decision list, amended (replaces my D1–D6)

| # | Decision | Recommendation |
|---|---|---|
| 1 | Ship Slice 0 before the boundary decision? | Yes, as **0a** (web on incl. deny removal; prune non-core alsoAllow names; AGENTS dead text out; A02-remote via web_fetch on the public repo) then **0b** (record-retrieval tool, A10). |
| 2 | Model change timing | opus-5 first, alone, one day, announced as a dev change on the shared gateway; no overlapping personal-agent experiments; note shared subscription quota. |
| 3 | Where native MC runs | Dev-only on personal gateway with exit condition; no-unscheduled-restart rule now. |
| 4 | Boundary for Slice 1 | One-session F2 test (incl. `sessions_spawn` carve-out); fallback F3 as **fresh public clone**, `workspaceOnly` kept. Reject C. |
| 5 | Persona canonical location | `services/mc_operations/personas/mc-chat/releases/<version>/` with `supported_runtime` + portable/runtime split — **decide before Slice 0a**, which becomes release 0.1. Decide whether releases are public artifacts. |
| 6 | Build owner | Claude Code builds, Codex reviews. |
| 7 | Delegation/export | Successor slice under #361. |
| 8 | CoS fast follow first use | Career Narrative Kit, own spec, after Slice 0 passes, re-verified on 2026.7.1. |

---

## What I did not do

No F2 test, no config edit, no persona install. The `_working`/gitignore and config facts above were read from the live tree and `openclaw.json` today; everything else is design review from text.

**Delivery:** this file is in `03_reviews/` beside the Claude Chat review. Creation here is not receipt by Codex, Claude Code or Grok; pass it explicitly.
