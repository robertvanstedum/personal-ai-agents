# Career Narrative Kit — tools (CoS early capability)

Intent and tools are public; data is private. Real workbooks, resumes, letters and inputs live in the owner's private
career area, never in this repository. Spec: `docs/specs/spec_162_career_narrative_kit_2026-10-24.md` (Guild build
queue item 162). Every tool refuses to overwrite an existing file: new output, new versioned name.

The flow:

1. **Import the current resume** → rows for column 1 (plus the resume's header and its Education/Capabilities tail):
   `python -m domains.cos.career_kit.resume_import RESUME.docx INPUT.json`
   Add each role's current LinkedIn text and the LinkedIn/resume mismatch list (`agreement`) to INPUT.json. A row may
   also carry `"rewrite": [lines]` to pre-fill column 3 (the helper's v1).
2. **Generate the workbook**: `python -m domains.cos.career_kit.workbook INPUT.json WORKBOOK.docx [--carry-from OLD.docx]`
   Owner: column 2 (notes), column 4 (what ships), and the Decision column of the agreement check. `--carry-from`
   builds a new version keeping the owner's columns 2–4, speaking points and decisions, matched by row id; it refuses
   if a row holding owner text would disappear.
3. **Preview or export**: `python -m domains.cos.career_kit.export INPUT.json WORKBOOK.docx OUTDIR [--draft] [--template RESUME.docx]`
   → `resume.docx`, `linkedin.md`, `report.md` (page count, rows without text, LinkedIn length flags, decisions).
   `--draft` previews from column 3, every output marked DRAFT. `--template` writes into the owner's own resume,
   keeping its layout, header and Education/Capabilities exactly (recommended: the resume the workbook came from).
   Rows without text are never filled with old or AI text.
4. **Assemble a cover letter**: `python -m domains.cos.career_kit.letter PARTS.json LETTER.docx` — posting-side
   opening, the owner's fixed middle, optional extra paragraph, close. A placeholder opening is refused.

Column 3/4 conventions (read by the export): `LinkedIn:` paragraph = LinkedIn entry; a bullet (typed `•`/`-` or made
with Word's list button) = resume bullet; `Note:` = editorial note, never exported; other paragraphs = resume entry.
In the About row: `Headline:`, `About:`, `Summary:`.

Page count: rendered headless with LibreOffice (`brew install --cask libreoffice`); without it the report says "not
checked". On the one file checked in Word (24 Oct), both gave 3 pages; Word can still differ slightly.

Tests: `tests/cos/test_career_kit_workbook.py` (placeholder data only). Dependencies: `python-docx`, `pypdf`, declared in `requirements.txt`.
