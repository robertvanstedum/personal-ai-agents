# Hand-back: finalize the MC redirect spec, then build

**22 Oct 2026, from Robert via Claude Code.** Order: **Codex → Claude chat → Grok → Robert approves → build.** Each pass is versioned beside the spec; nothing in this packet authorizes a build.

## 1. What is being finalized

`SPEC_MC_REDIRECT_v1.0_IMPLEMENTATION_2026-10-22.md` (Codex) plus the shared domain API v0.1 and the three domain pilots, folded with:
- Claude Code's review `reviews/claude-review-2026-10-22/MC_REDIRECT_SPEC_REVIEW_v0.1_2026-10-22.md` (v0.1b): verified claims, findings P1–P3, the "first light" milestone (§5), Robert's latency rule and budget (§8a), decisions (§7);
- `reviews/claude-review-2026-10-22/PERSONA_FILE_CONTRACT_2026-10-22.md`.

## 2. Decisions Robert took today (record them as decided in v1.1)

| # | Decision |
|---|---|
| D7 | **First light** is the first milestone: M1.0 baseline → M1.1 route → M1.2 policy + exact yes + batch guest → M1.3 tier-2 preferences → M1.4 the six exchanges + §7a on dev. The heavier state and slices (invocation table, outbox, context-pack budgets, provider swap, standing assessment, production promotion) come after. |
| D8 | **Latency rule:** the conversation path carries no authority machinery; the layers run only inside a tool call. Latency budget (review §8a) adopted as an M1 gate. Consequence: the conversation keeps a **stable runtime session**; fresh-session-per-dispatch is not the default (measure before any change). |
| D9 | **Persona:** Robert writes it with Claude chat, in his words, versioned in GitHub, per the persona file contract. After the spec is final. |
| D10 | **CoS tools:** CoS needs a real tool dispatch before any CoS domain slice can claim CoS (new slice D0; review P1-2). Backend choice still open. |
| D11 | **Production MC runtime** is a separate decision and change record; not needed for M1. |
| Done | **M1.1 is on dev** since 22 Oct 10:5x CDT: `MINIMOI_GUILD_MC=openclaw`, streaming on, native `mc-chat`; measured 2.8–5.7 s per conversation turn versus 12 s before. Record in `~/minimoi-runtime/operators/dev-mc-native-route-20261022/`. |

## 3. What each pass should do

**Codex (v1.1):** fold the review's findings and D7–D11 into the spec: the M1 slice order and exits; the stable-session default and the ten-turn test; D0 "CoS tool bridge"; the one-pending-offer rule for a plain yes (review P3-7); the acceptance additions (A08b, A15+, A02+, B1 exit+, P2-5 lock proof); the three-clock constant; the German pilot split (store migration first, API second). Say explicitly where you disagree and why.

**Claude chat:** does v1.1 still serve the partner intent, and does the first-light sequence show it early enough? Check the language clause and the persona contract against what Robert wants.

**Grok:** authority and recovery boundaries after the changes: stable session with code-required approval, tier-2 standing grants (`mandate_ref`), the batch guest card, the lethal-trifecta placements, production runtime isolation.

**Robert:** approves v1.1 and names the builder.

## 4. Who builds (Claude Code's recommendation, for Robert's decision)

Split by knowledge, not by preference:
- **First light (M1.0–M1.4): Claude Code.** It is integration of code Claude Code built this week (tool path, proposals, lessons, the route it switched today) plus two small new pieces (policy file v0, `keep_preference`). Codex reviews each slice against its own spec, Grok the authority points. Robert is in the room, which keeps the loop short.
- **After M1 (dispatch identity, invocation table, domain APIs, standing assessment, production runtime): Codex builds** what it designed; Claude Code and Grok review.
- **One writer per branch at a time**, whoever it is; the other never touches that branch.

The reason not to let the same agent build and review a slice stands either way.

## 5. Not in this packet

No code, no deployment, no production change. The persona session follows the spec's approval (D9).
