# Change note — Case study v9 (Claude.ai final pre-testing refinement)

**Baseline:** `CASE_STUDY_DRAFT_v8_CLAUDE_AI_2026-09-05.md` (sha256 `0622a47a…9f7d8`, verified identical to the packet copy)
**Result:** `CASE_STUDY_DRAFT_v9_CLAUDE_AI_2026-09-05.md` (sha256 `c9af69fc…2e4e6`)
**Scope:** the seven required edits in the handoff, plus the time-basis qualifiers those edits imply. Nothing else changed. Ten paragraphs touched; structure, title, numbers, and limits preserved.
**Status:** living draft. Not final.

## Edits applied

| # | Handoff item | Where | What changed |
|---|---|---|---|
| 1 | "The next build was that test" | §From lower-consequence trials, ¶3 | → "The next build raised the stakes and prepared that test." Exact wording from Codex and Grok. |
| 2 | "No agent merged its own work" | Opening, ¶2 | → "No agent merged any work." |
| 3 | File arithmetic | §Evidence, ¶2 | → "Together the reviewed tree contains 61 unique paths — five W0b paths revise files introduced in W0a — and adds 18,514 lines: …" Line total and package/test split unchanged. |
| 4 | Self-referential paragraph | §The operating method, final ¶ | Replaced with Codex's approved wording. One tense change from Codex's text: "Grok reviewed it" rather than "reviews it next," because Grok's review is now in the record. Claims only authorship separation, independent review, and human authority — not probes or a merge gate for prose. |
| 5 | Early status qualifier | §From lower-consequence trials, ¶3, after the no-dependency sentence | Added: "Of its two slices, the first was merged and the second had been independently reviewed but not yet merged at the recorded fact-check point; the evidence section gives the exact state." Closes Grok finding 5 without moving the table. |
| 6 | PR ranges | §From lower-consequence trials, ¶1 | Guild increments attributed to PRs #195–#198; IoT Connect promotion to PR #199. No further release detail added. |
| 7 | Generic paragraph | §What scales, ¶1 | Rewritten to name this run's concrete anchors: hash-pinned design, commit-named handoffs that survived crossed reviews, reproduced defects, Robert's merge decision. Same paragraph length; the second sentence's meaning retained. |

## Time-basis qualifiers (implied by edit 5)

- Evidence table header: "Status at fact check, September 5" → "Status at last recheck, September 5, 10:19 CDT."
- Limits section: "not merged at the time of the fact check" → "still open at the last recheck, September 5 at 10:19 CDT."
- Header line now reads `Status: Draft — living draft awaiting file-level and real Career-use evidence`, per the GitHub destination plan.

These three carry the handoff's own recheck timestamp. **Claude.ai could not independently recheck PR #201** — the repository's PR state is not reachable from this surface. See the readiness note.

## Not changed, deliberately

- Title, section order, and all headings.
- Every number: 38 / 25 / 13, 11 `REVISE` across 14 documents, 10 handoffs (5+5), W0a implementation revision four, all line counts, both test runs, 2,862-line pinned design, three declared divergences.
- The authorship-defect narrative and the three secondary findings.
- The important/exploratory contrast and the hands-on-keyboard sentence.
- The next proof, in future tense, ending on "Start cover letter."
- The closing sentence. Grok's "One thing to cut" was interrupted after the word "That" and its intent is unknown. Per the handoff, the close stands until Robert supplies the missing feedback. If Grok meant the final paragraph, cutting it is a one-line change and the document ends cleanly on the preceding paragraph.

## Grok findings not requiring an edit

Findings 6–9 (defect coherence, secondary findings proportionate, serialized mutation clear, product premise argued not asserted) were confirmations. Finding 10 (Guild/IoT Connect outside the evidence table) is addressed by edit 6. Grok's optional second add — naming the incomplete fix as a read-only view over a mutable mapping — was already present in v8 and is unchanged.

## Length

v9 is ~1,810 words including header and table, against v8's ~1,710. The increase is the status qualifier (edit 5), the PR attributions (edit 6), and the arithmetic clause (edit 3). Still under the five-page ceiling. Codex's note that a later publication pass could remove 150–250 words stands; that pass should wait for testing evidence per Robert's direction.
