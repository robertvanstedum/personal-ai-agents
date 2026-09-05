# Public-readiness note — Case study v9

**Prepared by:** Claude.ai, 2026-09-05
**Applies to:** `CASE_STUDY_DRAFT_v9_CLAUDE_AI_2026-09-05.md` and its eventual public copy `docs/portfolio/mini-moi-collaborative-work/CASE_STUDY_AGENT_TEAM.md`
**Purpose:** name every fact that must be refreshed after user testing or immediately before release. Not a review of the prose.

## Must refresh immediately before any public commit

**1. PR #201 status.** This is the only fact in the document with a known short half-life. v9 states it as open at 10:19 CDT on September 5, using the handoff's recheck; Claude.ai could not verify it independently. If it has merged, update **all four** of these together, in one pass:

- the evidence table row for W0b (status cell and header timestamp);
- the early status qualifier in §From lower-consequence trials, ¶3;
- the opening sentence of §What has not been proved;
- the phrase "against the merged service" in the next-proof paragraph, which currently reads as future-conditional and would become simply true.

If it has merged, also add the merge commit and time in the same format as W0a's row. Do not update any one of these without the others — a half-updated document is worse than a dated one.

## Must refresh after file-level acceptance

**2. The next-proof paragraph.** Currently future tense: "First, a file-level acceptance test against the merged service." Once that test has run, this sentence becomes past tense with a result, and the limits section loses one of its "has not been proved" items. The document should say what the acceptance test found, in one sentence, including if it found nothing.

**3. "Nothing is connected to the running Chief of Staff."** True until the thin integration lands. Refresh when it does.

## Must refresh after real Career use

**4. "No private Career material has entered the service, and no cover letter has been produced through it."** This is the sentence that makes the limits section credible today. When it stops being true, replace it with what actually happened — including revision count on the first real letter, since the vision commits to that being roughly ten and honesty about the number is part of the argument.

**5. "The evidence so far comes from synthetic data and adversarial tests."** Refresh alongside item 4.

**6. The closing paragraph's claim about "the deeper test."** Unchanged in wording, but it shifts from prediction to something with early evidence. Robert's direction is to hold substantive narrative changes until testing produces evidence; this is where that evidence goes.

## Stable — should not change without a new fact check

All counts and identifiers in the evidence section: 38 / 25 / 13, 11 `REVISE` across 14 documents, 10 W0b handoffs, W0a implementation revision four, PR #200 at `476568d`, `c9cdde7`, all file and line counts, both test runs, the 2,862-line design, three declared divergences, PRs #195–#199. These were verified by Codex against the preserved record and should be treated as frozen unless the record itself changes.

## Before the public commit — checks that are not about facts

- **No private path or identifier leaks.** The public copy must not contain `_working/` paths, Planning Studio ULIDs, handoff filenames, or the local review-packet names. v9 contains none, but the public copy is a separate file and should be grepped independently.
- **The companion vision goes public alongside it.** The case study references "the companion vision document" and "a companion document." If `MINI_MOI_WORK_VISION.md` is not in the same commit, those references dangle.
- **`Status: Draft` is inside the document,** not only in the filename or commit message, per the destination plan. v9 carries it in the header line.
- **Grok's interrupted final item.** The record preserves that Grok's "One thing to cut" ended after "That." If Robert obtains the missing text and it names the closing paragraph, that is a one-line change. Until then the close stands and the interruption is recorded as unknown.

## One thing this note does not do

It does not recommend cutting length. Codex's estimate that 150–250 words could come out is likely right, and it should happen in the same pass that adds testing evidence — not before, because the passages most likely to tighten are the ones testing will rewrite anyway.
