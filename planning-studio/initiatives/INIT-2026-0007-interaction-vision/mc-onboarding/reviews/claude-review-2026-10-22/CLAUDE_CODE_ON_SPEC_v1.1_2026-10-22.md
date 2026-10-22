# Claude Code on spec v1.1 (22 Oct, afternoon)

**Input to the final review** (Codex v1.1 → Claude chat → Grok → Robert). Claude Code is the M1 builder (D12), so this is the builder's position on the contract it will build to, not a second review of its own work.

## Verdict

**Accept v1.1 as the build contract for first light, including Codex's three corrections.** One of them corrects a claim of mine. Nothing below asks for a further revision before Claude chat and Grok read it.

## The three corrections

| Codex | My position |
|---|---|
| **C1. Stable sessions still need per-run action binding before tools act.** A delayed call from old run A can bind to new turn B; a code on tier-3 approval doesn't stop a tier-2 write, a misattributed proposal, or a private read under B. | **Correct, and it corrects me.** In the review (§8a) I wrote that the code-required approval already covers the binding ambiguity. It covers tier-3 *effects* only. Reads and tier-2 writes are not covered. Codex's candidate (the opening stream run ID plus the `before_tool_call` hook, forwarded as `X-MC-Run`/`X-MC-Tool-Call` and bound once to the claim) is the right shape, and its gate is the right gate: the installed-runtime probe with a deliberately delayed old callback (A05), fake provider, no cost. **Build order inside M1.2: the probe first, tools only after it passes.** Plain conversation (M1.1) is unaffected. |
| **C2. Display proof can't be deferred.** A proposal is approvable only once its platform note is durably present and read back; crash windows must reconcile by a deterministic key. | **Correct, and small.** T already posts the card with the deterministic key `mc-tool-proposal-<id>` and withdraws the proposal when the note can't be kept. What's missing is the `display_state` column, the read-back check, and the recovery on approval inspection. That is a day's work, not the general outbox. |
| **C3. M1 cannot claim six of six while the Wörter save is deferred.** | **Correct.** Exchange 5 is "five plus one deferred" at M1; D1 completes it. One addition so the M1 trial still tests something for exchange 5: the pass condition at M1 is the production-support answer ("Wörter exists; saving from chat isn't wired yet; shall I open a work item?"), which proves house knowledge and honesty, not the save. Label it exactly that. |

## The others

- **C4, two clocks** (ten-minute offer, five-minute execution window from the approval, both C07 fields set to that deadline): accept. It is a behaviour change from T; I'll test both boundaries as §9.2 lists.
- **C5, `mandate_ref` changes grant digests:** accept; post-M1, with a format version.
- **C6, the dev route is an operator overlay, not reproducible source:** accept. The two variables live in `~/minimoi-runtime/operators/dev-mc-native-route-20261022/new.json`; the reproducible home is the staging config file that set `cos` on 13 Oct (`~/minimoi-staging/config/vault-mc-dev-5239cbfc.yml`). Small operator task, dev only, on Robert's OK.

## Two notes for Claude chat and Grok

1. **For Claude chat:** v1.1 §9.2 makes a plain "yes" narrower in M1 than the product intent (codes still required after any GitHub read in the conversation, and while the runtime isn't isolated). That is the honest state while C1 is unproven; it should read to Robert as a temporary restriction with a stated exit, not as the design. The persona contract and A08b are unchanged.
2. **For Grok:** the probe in §7.1 is the hinge of the whole first light. If the installed runtime can't deliver the run ID before the first tool call reliably, the fallback in v1.1 is "actions stay disabled, conversation stays usable", which I support. Please press on the 2-second wait at the tool boundary (step 4) and on forged run IDs.

## Baseline status (unchanged)

`claude/mc-first-light-m1` at `28346c31`, pushed. Fast suites 156 passed / 76 skipped, plugin 10/10. PostgreSQL suites, full regression and the runtime probe remain blocked by disk (6.4 GiB free, gate 8); they run before M1.0 is called pinned.
