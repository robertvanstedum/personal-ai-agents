# Career Narrative Kit — Chief of Staff early capability, spec v0.2

**Specification ID:** pending (allocate from the Guild build queue; rename this file then) · **Domain:** Chief of Staff (CoS), early capability · **Status:** draft v0.2, 24 October 2026 (Claude Chat review K1–K7 folded in; Grok review pending) · **Reviewers:** Grok, Claude Chat (Codex reviewed 24 October) · **Source design:** Claude Chat with the owner; publication copy prepared by Codex; registered by Claude Code.

The original v0.1 is preserved in the private working home. This derivative removes personal examples and a private incident, makes publication of research deliberate, and avoids claiming an already implemented CoS capability. These editorial changes are proposals for the reviewers; substantive revisions follow as v0.2.

Rule for this document: **intent and tools are public; data is private.** The generic method, blank templates and non-personal research questions are candidates for the public repository after review. The filled-in workbook, drafts, target lists and personal notes live in the private career area and are never committed.

## 1. In plain words

A career is usually presented as a list of jobs. For someone whose jobs were all change situations, that list hides the one thing that makes the career coherent. This kit is a method, with templates, for rebuilding a professional story around that thread, job by job, in the person's own voice, and then producing every outward-facing artifact from one source: LinkedIn, resume variants, cover letter, project blurb.

It is a candidate early career capability for the Chief of Staff (CoS) domain: helping an owner say who they are to the market, and researching where that market is going. The first delivery is a standalone authoring method and generator; CoS runtime integration is later and is not claimed implemented. The method is written so it works for anyone.

## 2. Design direction

- Build a coherent professional narrative from concrete situations and verified contributions, rather than generic leadership labels.
- Work role by role before writing a summary.
- Reuse an approved narrative across LinkedIn, resume variants and a cover-letter base without erasing channel-specific differences.
- Preserve the owner's voice and final editorial decision.
- Support more than one emphasis while keeping titles, dates and underlying facts consistent.

## 3. The method

### 3.1 The workbook

One table, landscape, one row per job plus a last row for the About/summary. A narrow first column carries the job metadata (title, employer, dates, place); the four authoring columns follow:

| Column | Who writes | What goes in it |
|---|---|---|
| 1. Current text | kit | The LinkedIn entry and the current resume text for that job, side by side, so both get fixed at once. |
| 2. Owner notes | owner | Highlights, corrections, what was actually changing, what was won, what is only a speaking point. Raw. |
| 3. Rewrite | CoS / AI | A rewrite from columns 1 and 2, following the rules in §3.2. |
| 4. Owner final | owner | The owner's edit of column 3. This is the text that ships. |

Order of work: most recent job first, oldest last, **About/summary last**. Written first, the summary drifts back into boilerplate; written last, it is the summary of stories that already exist.

### 3.2 Writing rules

1. **Change line first, when there was a change.** Every job opens with one short line naming what was changing: an acquisition, a restructuring, a new market, a cut-over. Lined up down the page, these lines are the thread. Where the evidence does not support one, the row opens with a neutral factual line; the method does not invent change to fit the formula.
2. **Situation, then operating style, then proof.** Lead with the situation, not the skill. A broad skills claim ("works across business and technology") is something anyone can say; it goes second, as how the person operated inside the situation. Proof is one or two named examples.
3. **Concrete nouns over labels.** "Transformation" is soft; every consultancy uses it. Say acquisition, cut-over, re-established operations, new market. Let the reader conclude "transformation."
4. **LinkedIn text is the entry line; resume adds two or three bullets.** Both must agree on titles, dates and places.
5. **Wins on the page, the rest as speaking points.** Each job keeps a short off-page list of details for interviews.
6. **Owner's voice wins.** Column 4 is final. The AI's job is a v0 the owner can cut.
7. **No new facts in column 3.** The rewrite uses only what is in columns 1 and 2. It may not add numbers, outcomes, ownership or causality the owner has not supplied. Where the owner's notes mark a fact as unconfirmed, the rewrite carries the mark.
8. **Nothing confidential reaches column 3 unless the owner clears it.** Current-employer internals, client names under agreement and unreleased figures stay in column 2 or in speaking points until the owner says they can be used.

### 3.3 Outputs from a finished workbook

| Output | Source rows |
|---|---|
| LinkedIn About and experience entries | all rows |
| Personal, first-person cover letter (generic base) | About row, two or three proof lines |
| Resume, long version | all rows, column 4, with bullets |
| Resume, short version | change lines plus one bullet per job |
| Project blurb | its own row, treated like a job |
| Headline and resume title line | About row |
| ATS-safe resume (single column, no tables) | all rows, column 4 — the workbook is the authoring surface, never the file that is sent |

Variants (an industry, a role family) are not extra columns. Each variant is a named version of the workbook, labelled in its header, with its own approved column 3 and column 4 text. Titles, dates, places and the underlying facts are shared across variants and change in all of them or none. Approved text is revised as a new version, never regenerated over.

An **Earlier career** row collects roles too old or too short for their own entry, so the table has somewhere to put them without dropping them.

## 4. Research track (CoS)

CoS keeps a small research file beside the kit. The working hypothesis, stated as a question to test, not a conclusion:

> As AI adoption forces organizational change, does demand grow for leaders who have repeatedly delivered and operated systems through M&A, restructurings, new-market launches and cut-overs, faster than the supply of people who have actually done it? Where does sponsorship for such roles sit, and which organizations are creating them?

Pieces, done over time, each a dated note with sources:
1. Demand for change and integration leadership roles versus AI engineering roles, 2025–2026.
2. How crowded the "transformation leader" label is, and what actually differentiates (operated through it versus advised; repeated M&A integration; operator and vendor side).
3. Who is creating these positions: PE operating teams, transformation offices, SIs, carve-outs, interim and fractional markets.
4. Evidence on where the mandate sits (top-down sponsorship versus hiring-manager level).
5. Survival outlook of the companies doing the hiring.

Research notes, employer assessments and target lists remain private by default. A separately reviewed, de-identified summary may be published with dated sources; this is never an automatic publication step. Evidence that challenges the working hypothesis must be retained alongside supporting evidence. Demand for roles, volume of postings and the owner's personal fit are three different claims and are recorded separately.

## 5. Tools

- `WORKBOOK_TEMPLATE.md`: the blank table (markdown).
- `resume_import`: current resume (.docx) → workbook input rows, plus the resume's header and its Education/Capabilities tail, carried through unchanged.
- `workbook`: input → landscape Word workbook. Refuses to overwrite; successors get a new name.
- `export`: workbook → ATS-safe `resume.docx`, `linkedin.md`, and `report.md` (rows still without a final, LinkedIn length flags, agreement decisions). Rows without an owner final are never filled with old or AI text.
- `letter`: posting-side opening + owner's fixed middle + optional project paragraph + close. A placeholder opening is refused.
- Checks: title/date/place agreement is surfaced as flags for the owner to decide, never auto-corrected. A resume page-count check is planned and needs a renderer; not in v0.2.
- Round trip: the Word workbook is read back by `export` only for the columns named above. Lossless regeneration from an edited Word file is not claimed.

## 6. Spin-offs (not in this spec)

- A CoS "career" page: the workbook as a live record, rows editable, with variants generated on demand.
- Rooms/Records: each job-row session filed as a record with the decision trail.
- Research pieces as scheduled CoS tasks (the existing "career scout" periodic task is the hook).

## 7. Not in scope

Automated application filling and submission are outside this authoring kit. This specification does not authorize external profile edits, job applications, account access or scheduled research.

## 8. Status and next steps

- v0.1 written October 24, 2026. Reviewers: Grok, Claude Chat; Codex reviewed 24 October.
- First instances and filled workbooks remain in the established private career workspace; no personal example is required to understand or test this method.
- Registration: after review and owner acceptance of v0.2, use the existing public documentation specification area for generic artifacts, and allocate the spec ID through the authoritative Guild build queue. Do not invent a new public career-data directory or derive an ID from a stale local queue copy. Personal instances remain in their existing private workspace.

## 9. Tooling (built, draft)

`domains/cos/career_kit/` — resume import, workbook generator, export of owner finals (single-column resume, LinkedIn
text, report) and cover-letter assembly, each refusing to overwrite. See its README. Tests use placeholder data only.
A dry run of the whole flow on a real private instance passed on 24 October (private record, not in this repository).

## 10. Review status and known v0.2 changes

Claude Chat's review (K1–K7, 24 October) is folded into this v0.2; Grok's review is pending. Already identified (Codex and Claude Code reviews, 24 October): evidence
grounding for every claim (a neutral opening when there was no change); named variants with their own approved
wording; never overwrite owner text (implemented); explicit layout (implemented: job metadata in its own column,
14/28/20/20/18 %); workbook separate from the ATS-safe resume (implemented); a confidentiality rule (nothing
confidential reaches the rewrite unless the owner clears it); an "Earlier career" row; headline and resume title line
as outputs (implemented in the About row); per-channel length flags (implemented for LinkedIn); research optional.
