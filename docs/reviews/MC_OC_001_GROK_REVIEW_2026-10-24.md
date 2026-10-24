# Grok review — MC OpenClaw reorganization and Slice 0

**Author:** Grok Chat  
**Date:** 2026-10-24  
**Reviewed:**  
- `SPEC_MC_OPENCLAW_REORGANIZATION_v0.1.md`  
- `CLAUDE_CHAT_REVIEW_MC_OC_001_v0.3_2026-10-24.md`  
- `PERSONAL_OPENCLAW_RESPONSE_TO_CLAUDE_CHAT_REVIEW_2026-10-24.md`  

**Status:** Design review. No implementation, configuration change, or deployment is authorized by this document.

---

## Verdict

**Directionally right; Slice 0 is the correct next move, with one structural adjustment.**

The reorganization spec and the two reviews are aligned on the important point: MC needs usable investigation and continuity before more boundary machinery. Claude Chat’s R1 (Slice 0) and the personal OpenClaw response’s A1 split are the most useful contributions in the packet. The consolidated decision list is better than the three overlapping ones it replaces. Nothing here reopens the earlier authority model; it is a practical sequencing layer on top of it.

---

## What is solid

- **Slice 0 is the right first increment.** Web tools on, dead AGENTS text removed, and a minimal record-retrieval tool are the changes Robert will actually feel. They do not require the §6.1 boundary decision and do not weaken the existing Board do-and-tell behavior.
- **Public-clone boundary is the correct simplification.** A fresh clone of the public remote cannot contain `_working/`, `.env`, or unpushed private branches. That is a real, enforceable reduction in scope. The personal OpenClaw verification of gitignore and remote status supports it.
- **Model change as its own step is correct.** Swapping to a stronger model before the persona release isolates one variable. Shared gateway and shared subscription quota are real constraints and should be recorded, not ignored.
- **Persona location decision belongs before Slice 0a.** Editing AGENTS.md in place is a persona change. Treating Slice 0a as the first versioned release (or explicitly labelling it a baseline patch) prevents the out-of-process edit problem that already occurred.
- **CoS first use is usefully concrete.** Naming Career Narrative Kit gives the CoS fast-follow something real to aim at instead of another abstract governance exercise.

---

## Adjustments and challenges

### 1. Slice 0a / 0b split is necessary, but 0a is still not free of decisions

The personal OpenClaw response is right that the record-retrieval tool is new MiniMoi server code with scope rules and negative tests, while web + AGENTS cleanup are config/text. However, even 0a touches persona (decision #5) and the public-clone implication for persona releases (A3). Those two decisions should be taken before or as part of 0a, not deferred. Otherwise the “fast slice” still waits on a policy choice about where the canonical files live and whether they are public artifacts.

### 2. Remote investigation via web_fetch is real but partial

A02-remote against the public GitHub raw content is achievable in 0a and is a legitimate Mac-independent path for pushed revisions. It does not replace local-checkout investigation for unpushed work, and it does not give MC the ability to follow internal cross-references that exist only in a worktree. Claim A02-remote explicitly and leave local investigation to Slice 1. Do not let the remote path become the assumed default for “which spec is deployed?” when the interesting revision is still unpushed.

### 3. Shared gateway and shared quota need an explicit observation rule

Switching mc-chat to a stronger model on the same gateway and the same Anthropic subscription means any throttling or latency change during the one-day observation window is confounded with personal-agent use. The personal OpenClaw note is correct. Add a short rule: the observation window is announced, personal-agent model/auth experiments are paused or logged, and any rate-limit event is recorded as a host artefact before any claim is made about model quality.

### 4. Prompt-injection residual risk is accepted but should have a minimal control

Claude Chat’s R6 is right: once web_fetch is on, the only control on “no private context in public search queries” is prompt text. Logging outbound search queries and fetched URLs where an operator can read them is cheap and gives A05 evidence rather than pure assurance. That belongs in Slice 0a, not later.

### 5. Record-retrieval tool scope is the highest-leverage remaining design point

The simplest candidate (search over Spec 160 turn log + Records, read-only, MC’s own conversations + Guild decisions) is directionally correct. The negative tests (CoS cannot see MC’s private operational turns and vice-versa) are the actual safety property. This should be specified tightly enough that the implementer does not invent a second memory system or a cross-agent view. It does not need to be perfect continuity; it needs to let MC retrieve a prior decision without Robert re-explaining it.

---

## Consolidated decision list — recommended form

| # | Decision | Recommendation |
|---|---|---|
| 1 | Ship Slice 0 before the boundary decision? | Yes, as **0a** (web on + deny removal; prune non-core alsoAllow names; AGENTS dead text out; A02-remote via web_fetch; outbound query/URL log) then **0b** (record-retrieval tool with explicit scope and negative tests). |
| 2 | Model change timing | Stronger model first, alone, one day, announced as a dev change; pause or log personal-agent experiments; record any shared-quota events. |
| 3 | Where native MC runs | Dev-only on personal gateway with an exit condition; no-unscheduled-restart rule now. |
| 4 | Boundary for Slice 1 | Time-boxed one-session F2 test (including sessions_spawn carve-out). Fallback is F3 as a fresh public clone with workspaceOnly retained. Reject option C. |
| 5 | Persona canonical location | Decide before Slice 0a. Recommended: `services/mc_operations/personas/mc-chat/releases/<version>/` with supported_runtime and portable/runtime split. Decide whether releases are public artifacts. Slice 0a becomes release 0.1 or is explicitly labelled a baseline patch. |
| 6 | Build owner | Claude Code builds, Codex reviews. |
| 7 | Delegation/export | Successor slice under #361. |
| 8 | CoS fast follow first use | Career Narrative Kit, own spec, after Slice 0 passes; re-verify capability set on the CoS runtime version. |

---

## Bottom line

Approve the direction and the Slice 0 sequencing. Take the persona-location decision and the public-artifact decision before or as part of Slice 0a so the fast slice is actually fast and does not create another out-of-process edit. The record-retrieval tool is the piece that makes “keep the thread” real; keep its scope tight and its negative tests explicit. The boundary question (F2 vs. public-clone F3) can wait until after Robert has used the improved MC for a few days.

This is ready for a short final reconciliation and then a build plan for Slice 0a. Production and the full §6.1 boundary remain later.

---

*This is a design review for discussion and for folding into the next version of the reorganization spec. Nothing in it authorizes implementation, a configuration change, or a deployment.*
