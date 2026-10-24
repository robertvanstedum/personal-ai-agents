# Claude Chat review — MC-OC-001 v0.3

**Reviewer:** Claude Chat (Fable 5.1), in Robert's session, 2026-10-24
**Reviewed:** `docs/specs/spec_mc_openclaw_operating_model_2026-10-24.md` v0.3, SHA-256 `36d6409050d98e8587ed96950f480f47b1cf251cd8f2d5132e8cb0007eb4b575`, commit `06d8bd09`, branch `codex/mc-openclaw-operating-model-spec`
**Also read:** Claude Code v0.2 review and Codex v0.3 disposition; personal OpenClaw review v0.3 (F1–F6, D1–D6); REVIEWER_BRIEF; CODEX_CONFIG_RECONCILIATION
**Nothing installed, changed or restarted for this review.** Design review from supplied text only.

## Verdict

Adopt the direction. The architecture is right and the two prior reviews are good; I agree with Claude Code's B1–B3 and with OpenClaw's F1–F6 and don't repeat them. My findings are about the gap between this spec and MC actually improving in Robert's hands, and about collapsing three overlapping decision lists into one.

The spec is now carrying more gate than product. #361 says MC does not fulfill day-to-day ownership. Reading v0.3 plus the reviews, the honest forecast is that nothing Robert can feel changes until the §6.1 boundary question is investigated, decided, built and passed through A01–A12. That is weeks. Part of the work needs none of that and can ship first.

## Findings

### R1 — Split out a Slice 0 that needs no boundary decision

Three changes are independent of §6.1 options A/B/C, have verified mechanics, and are the ones Robert will notice:

1. **Web tools on** for mc-chat: `web_search` and `web_fetch` added to `alsoAllow`, `group:web` (and any individual deny) removed. Mechanics confirmed by Codex and Claude Code. A04 checks it.
2. **AGENTS dead text out:** remove the JOB/ASK/MEETING routing, the "every mutation makes an offer" line, the twenty-second cutoff, and tool names not in the configured plugin list. No new capability promised; just stop lying to the model about what it has.
3. **One record-retrieval tool** (OpenClaw F4). Without it, "keep the thread" has nothing behind it. Simplest candidate: a search over the Spec 160 daily turn log and Records, read-only, scoped to MC's own conversations and Guild decisions.

Proposed text for §10, delivery table, new first row:

> | Slice 0 (no boundary decision required) | Web tools enabled with both allow and deny effects; AGENTS/SOUL reconciled with dead routing removed; one read-only record-retrieval tool for MC's own prior conversations and decisions; Board and memory behavior preserved | A04, A06, A10, A11; persona diff reviewed; no §6.1 selection needed |

Then the current "First MC slice" becomes Slice 1 and carries the boundary work. Nothing in Slice 0 pre-empts the F2/F3 decision.

### R2 — Time-box the boundary decision; the public repo is already the boundary

OpenClaw's F2 (mc-chat delegates to sandboxed mc-agent via `sessions_spawn`) is the best candidate and has one caveat that decides it: whether the child inherits mc-chat's deny ceiling. That is a one-session test, not an investigation. Proposed rule for §6.1:

> The F2 compatibility check is time-boxed to one working session. If ceiling inheritance blocks it, Option A in the F3 form (dedicated read-only checkout, `workspaceOnly` retained) is adopted without further deliberation.

On F3 itself, one simplification: `personal-ai-agents` is a public repository. Anything in its remote history is by definition not secret. So the enforceable boundary for "authorized project source" is a **fresh clone of the public remote**, not a worktree of Robert's working checkout. A worktree shares `.git` with the working copy, including local-only branches and whatever private material those may carry; a fresh clone cannot see them. Exclusions then reduce to: no `.env*`, no databases, no `_working/` from the private tree, because none of those are in the clone to begin with. That makes A05's "completeness and exclusions" test short.

### R3 — Change the model before the persona, as its own step

§11 correctly keeps model selection out of this spec. But sequencing matters for attribution. Robert's complaint is partly "MC feels passive." If model (sonnet-4-6 → opus-5, now unblocked per OpenClaw D6) and persona change in the same release, nobody can say which one helped, and the A/B in §9 is confounded from the start. Recommend: model swap first, alone, one day of ordinary use, before the Slice 0 persona release. It is the cheapest experiment available and it isolates one variable.

### R4 — F1 is a decision for Robert, and it affects A/B validity

Agree with OpenClaw F1. Dev MC on the personal gateway means every A/B between MC and Mini-moi shares host, token, plugins and restarts. Until MC has its own gateway or moves to the container, §9's A/B results are indicative only. The spec should say so in §9 rather than leave it to the reader.

### R5 — Reviewers can't review the persona from this package

§6.4 names OpenClaw's suggested SOUL as the principal input, but SOUL is in private evidence, not in the circulation set. Grok reviewing from GitHub sees the contract for a persona, not the persona. Acceptable if intentional (B3 defers the reconciled SOUL/AGENTS to the build-plan package), but say so in §11 so the next reviewer doesn't look for it.

### R6 — Prompt injection becomes live the day web_fetch is on

§6.1's last paragraph has it right in principle. In practice the only control on "no private context in public search queries" is prompt text. For Slice 0, accept that as a known residual risk and add one cheap control: log outbound search queries and fetched URLs where an operator can read them. A05 then has evidence, not assurance.

### R7 — CoS fast follow should have a named first use

§8.4 defines the CoS adoption contract well but abstractly. A concrete first use keeps it from becoming a second governance exercise. Candidate, already drafted today: the Career Narrative Kit (CoS helps its owner say who they are to the market, and researches where that market is going). It needs exactly the Slice 0 capabilities: web, record retrieval, versioned persona. Propose naming it in §8.4 as the CoS fast-follow use case, with its own spec.

## Answers to the six brief questions

1. **Investigative partner without Robert carrying context?** Yes in design. In practice only once F4's retrieval tool exists; it belongs in Slice 0, not later.
2. **Enough real access, and which mechanism enforces the boundary?** Slice 0 needs none. For Slice 1: F2 if the ceiling test passes, else F3 as a fresh clone of the public remote. Time-box the test.
3. **Actions governed by consequence?** Yes. Delete the blanket-offer line. Keep Board's do-and-tell with conditional undo as the reference behavior.
4. **Persona and memory contracts avoid stale prompts and competing stores?** Yes with F5 (version pinning) and F6 (portable/runtime split). Add R3: model change as its own step so the persona diff is judged on the model it will run on.
5. **CoS fast follow reusable without leakage?** Yes as written. Give it a named first use (R7) so the addendum has a real scope.
6. **First slice and remaining decisions?** Slice 0 as in R1. Decisions consolidated below.

## One decision list for Robert

Three documents now each carry a decision list (spec §10, Claude Code's four, OpenClaw's D1–D6). Consolidated, with my recommendation:

| # | Decision | Recommendation |
|---|---|---|
| 1 | Ship a Slice 0 before the boundary decision? | Yes: web on, AGENTS cleaned, record retrieval. (R1) |
| 2 | Model change timing | Swap to opus-5 first, alone, one day, before the persona release. (R3, OpenClaw D6) |
| 3 | Where native MC runs | Personal-gateway hosting is dev-only with an exit; add the no-unscheduled-restart rule now. (OpenClaw D1) |
| 4 | Boundary mechanism for Slice 1 | Time-boxed F2 test; fallback F3 as a fresh public clone. Reject C. (R2) |
| 5 | Persona canonical location | `services/mc_operations/personas/mc-chat/releases/<version>/`, with `supported_runtime` and the portable/runtime file split. (OpenClaw D4) |
| 6 | Build owner | Claude Code. Codex reviews. (all three reviews agree) |
| 7 | Delegation/export | Explicit successor slice, tracked under #361; not in Slice 0 or 1. |
| 8 | CoS fast follow first use | Career Narrative Kit, own spec, after Slice 0 passes. (R7) |

Nothing above closes #361. Slice 0 is what makes MC feel different next week; Slice 1 is what makes the boundary real.

## What I did not do

Did not run any test, read the installed runtime, or verify the F2 ceiling behavior. R2's "public repo is the boundary" rests on the repository being public, which I confirmed by cloning it unauthenticated today; it does not cover anything Robert may later make private.
