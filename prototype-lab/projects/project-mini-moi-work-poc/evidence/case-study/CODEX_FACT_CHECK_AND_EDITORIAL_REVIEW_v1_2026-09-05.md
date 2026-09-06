# Codex fact check and editorial review

**Reviewer:** OpenAI Codex
**Date:** 2026-09-05
**Reviewed artifact:** `CASE_STUDY_DRAFT_v6.md` by Claude Code
**Resulting artifact:** `CASE_STUDY_DRAFT_v7_CODEX_2026-09-05.md`

## Verdict

Claude Code's draft has a strong argument and a compelling central example.
Revise the factual scopes before publication. The number 38 is supported, but
several component counts are labelled as though they cover both W0a and W0b
when they cover only W0b. The public version must also distinguish independently
cleared code from merged, integrated, deployed, and field-tested capability.

## Keep from draft v6

- The lower-consequence proof-test to consequential-work arc.
- Forged authorship as the central defect; it connects engineering integrity to
  the product premise.
- The rule that the code author does not approve the code and no agent merges.
- The emphasis on reproducible probes rather than reviewer opinion.
- The limits section and the argument that product design remains human-led.

## Required factual corrections

1. **Finding breakdown.** Thirty-eight is correct across W0a and W0b:
   - W0a checkpoint A: 6 design findings.
   - W0a checkpoint B: 1 implementation finding (`B3-1`).
   - W0b checkpoint A: 19 design findings.
   - W0b checkpoint B: 12 implementation findings.
   Therefore the combined split is **25 design / 13 implementation**, not
   19/12. The 19/12 figures are accurate only when explicitly scoped to W0b.
2. **Review refusals.** Fourteen preserved Codex review documents contain
   **11 `REVISE` verdicts** and three clearing verdicts. The draft's count of
   nine refusals is unsupported. W0b alone had eight `REVISE` verdicts.
3. **Revision count.** W0b has ten numbered handoffs: five checkpoint-A design
   revisions and five checkpoint-B implementation revisions. W0a is separate
   and reached implementation revision four. “Ten build revisions across two
   gates” is ambiguous and should not be used without this qualification.
4. **Current status.** W0a merged through PR #200 as `476568d` at 2026-09-04
   21:46 CDT. W0b was cleared by Codex at approximately 2026-09-05 02:53 CDT at
   `c9cdde7`; PR #201 is still open as of this review. Do not say both slices
   are merged, deployed, or complete.
5. **Change size.** Git reports:
   - W0a: 41 files, 6,329 additions.
   - W0b relative to W0a: 25 files, 12,202 additions and 17 deletions.
   - Combined final tree relative to `36d0a71`: 61 files, 18,514 additions.
   - Package: 8,366 additions under `domains/cos/work/`.
   - Tests and fixtures: 10,148 additions under `tests/cos/`.
   The rounded 8,400/10,100 values are fair only if described as added lines.
6. **Test evidence.** Builder: 695 passed/1 skipped in `tests/cos`, 1,318
   passed/18 skipped repository-wide. Independent Codex run: 695/1 and
   1,317/19. Both collected the same total and had no failures. A public draft
   should not silently present the builder's result as the independent run.
7. **Recovery wording.** The malformed-recovery probe changed canonical files
   in a synthetic temporary work tree. “Changing real files” suggests private
   or production data and is inaccurate. Use “changing canonical state” or
   “installing a candidate as canonical state.”
8. **Product status.** The code is a production-intent substrate for Chief of
   Staff work, not yet a production cover-letter capability. There is no
   runtime wiring, UI, model call, network access, or real Career use in W0a or
   W0b.
9. **Concurrency.** Planning, implementation, and review streams overlapped,
   but repository mutation remained serialized. Avoid language implying that
   several agents edited the repository concurrently.
10. **Role attribution.** Name the five participants, but do not imply that
    Grok reviewed the implementation. Grok challenged the early design;
    Claude.ai was the design and reconciliation partner; Claude Code authored
    the detailed W0b design and implementation; Codex performed the recorded
    independent design and code reviews; Robert owned intent, authorization,
    and merge decisions.
11. **Counterfactual claims.** “None would have been caught by reading the code”
    cannot be proved. The record supports the stronger factual statement that
    each implementation finding was made concrete with a reproducible probe.
12. **Length.** Draft v6 is 2,148 words, not approximately 1,780. Codex v7 is
    intentionally tighter in argument even though it retains an evidence
    section for employer-facing credibility.

## Verified chronology

- **2026-09-03:** PRs #195-#198 merged four Guild increments.
- **2026-09-04 00:44 CDT:** PR #199 merged the IoT Connect promotion; live
  release verification is preserved separately.
- **2026-09-04 21:46 CDT:** PR #200 merged W0a as `476568d`.
- **2026-09-05 00:59-02:43 CDT:** W0b implementation commits progressed from
  `25b3453` through `c9cdde7`.
- **2026-09-05 approximately 02:53 CDT:** Codex recorded W0b
  `READY_FOR_ROBERT` at `c9cdde7`.
- **At fact-check time:** PR #201 is open and unmerged.

## Editorial position for Claude.ai

Use the Codex v7 draft as the sole editorial baseline for the next round.
Claude Code v6 remains preserved as source history but is not part of the
Claude.ai packet. The final should remain Robert's case study, not an agent's
victory narrative. Its most distinctive claim is the connection between the
operating method and the mini-moi trust boundary: the team caught a mechanism
that could have taught the system to mistake generated language for Robert's
own voice.

The next-stage claim belongs in future tense: file-level acceptance, followed
by an end-to-end Chief of Staff interaction beginning with a job description
and the instruction “Start cover letter.”

Claude.ai's version should stand on its own for an external reader and should
not use a first-person narrator. Keep personality through concrete decisions,
specific defects, the contrast between important and exploratory applications,
and Robert's visible role. Use “Robert” and “the team” where agency matters;
do not flatten the piece into anonymous corporate prose.

The companion artifact
`robert-intent-and-mini-moi-work-vision--9bc367fd.md` controls the larger product
intent. Use it to preserve the relationship, accumulation, authorship, and
product-independence context. Do not treat its future-state language as evidence
that W0b is already integrated or field-tested.
