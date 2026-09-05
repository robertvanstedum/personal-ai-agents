# Grok review prompt — Case study v8

You are reviewing a portfolio case study that will be published on GitHub and may accompany job applications. The author of this round is Claude.ai; the factual baseline is an independent Codex fact check; the final editorial authority is Robert van Stedum. Treat nothing in the document as authoritative because of who wrote it.

Review the attached `CASE_STUDY_DRAFT_v8_CLAUDE_AI_2026-09-05.txt` skeptically, as both a technical reader and an editor. Do not rewrite it. Return findings.

## What you are checking

### Technical credibility

1. Every number, commit hash, PR number, timestamp, and file count in the draft was fixed by the Codex fact check. Flag any that appear inconsistent with each other or with the chronology as stated. You cannot verify them against the repository; flag internal inconsistency only.
2. The authorship defect is described as a mutable mapping inside an object marked immutable, with an incomplete first fix via a read-only wrapper over a mutable backing dictionary. Is this description technically coherent as written? Would an experienced engineer find it plausible, or does it read as jargon?
3. The three secondary findings — check-then-consume race on a one-time grant, short-write assumption in three write paths, malformed recovery record installing canonical state — are they described accurately enough to be believable and vaguely enough to avoid overclaiming?
4. The draft claims repository mutation was serialized while design, implementation, and review overlapped. Is that claim stated clearly enough that a skeptical reader would not assume concurrent editing?
5. The draft distinguishes "independently cleared" from "merged" and states PR #201 was open at fact-check time. Is that distinction clear on first read, or could a reader come away thinking both slices shipped?

### Narrative and audience

6. The intended readers are a hiring manager, a senior engineer on a panel, or a product leader — none of whom know mini-moi. Does the opening give them enough to follow without prior context? Where does it lose them?
7. The document is third-person by design. Does it retain a distinct voice through specificity and judgment, or has it flattened into corporate prose? Point to the flattest paragraph.
8. Is Robert visible as creator, collaborator, editor, authorization owner, and user — or does he read as an approver at the end of an agent pipeline?
9. The central claim is that the authorship defect connects engineering integrity to the product premise. Is that argument made or merely asserted? Is the "learning from itself" explanation convincing to someone who has not read the product vision?
10. The document says it was itself produced by the method it describes. Does that land as credible evidence of reusability, or as self-congratulation? Recommend keep or cut.

### Overclaiming

11. Identify every sentence that claims more than the evidence section supports. In particular: is anything presented as production capability when the limits section says nothing is wired, integrated, or tested with real data?
12. The final section states the next proof in future tense. Is anything elsewhere in the document written as if that proof had already happened?

## Response shape

- **Verdict:** publish as-is / minor edits / substantive revision needed.
- **Findings by impact**, each with the exact sentence or paragraph.
- **The flattest paragraph**, quoted, with one sentence on why.
- **Overclaims**, quoted.
- **One thing you would add** that would make an experienced engineer trust the document more.
- **One thing you would cut.**

Do not propose a full rewrite. Do not alter the numbers. Do not suggest first-person narration.
