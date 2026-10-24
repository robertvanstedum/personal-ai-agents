# Career Narrative Kit — tools (CoS early capability)

Intent and tools are public; data is private. Real workbooks, resumes, letters and inputs live in the owner's private
career area, never in this repository. Spec: `docs/specs/spec_162_career_narrative_kit_2026-10-24.md` (Guild build
queue item 162). Every tool refuses to overwrite an existing file: new output, new versioned name.

The flow:

1. **Import the current resume** → rows for column 1 (plus the resume's header and its Education/Capabilities tail):
   `python -m domains.cos.career_kit.resume_import RESUME.docx INPUT.json`
   Add each role's current LinkedIn text and the LinkedIn/resume mismatch list (`agreement`) to INPUT.json.
2. **Generate the workbook**: `python -m domains.cos.career_kit.workbook INPUT.json WORKBOOK.docx`
   Owner fills column 2 (notes); the rewrite goes in column 3; the owner writes column 4 (what ships) and the
   Decision column of the agreement check.
3. **Export the finals**: `python -m domains.cos.career_kit.export INPUT.json WORKBOOK.docx OUTDIR`
   → `resume.docx` (single column, no tables: readable by applicant tracking systems), `linkedin.md`, `report.md`
   (rows without a final, LinkedIn length flags, agreement decisions). Rows without a final are never filled with
   old or AI text.
4. **Assemble a cover letter**: `python -m domains.cos.career_kit.letter PARTS.json LETTER.docx` — posting-side
   opening, the owner's fixed middle, optional extra paragraph, close. A placeholder opening is refused.

Column 4 conventions (read by the export): `LinkedIn:` paragraph = LinkedIn entry; `•`/`-` = resume bullet; other
paragraphs = resume entry. In the About row: `Headline:`, `About:`, `Summary:`.

Tests: `tests/cos/test_career_kit_workbook.py` (placeholder data only). Dependency: `python-docx`, declared in `requirements.txt`.
