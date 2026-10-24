"""Career Narrative Kit workbook generator (spec: docs/specs/ career narrative kit, CoS early capability).

Intent and tools are public; data is private. This script writes the blank-to-fill authoring workbook: a landscape
Word table, one row per job (most recent first), project rows, then the About/summary row last. Column 1 carries the
current LinkedIn and resume text; columns 2 (owner notes), 3 (rewrite) and 4 (owner final) start empty. After the main
table come a speaking-points table and a LinkedIn/resume agreement-check table.

The workbook is for authoring only. Resumes sent to employers are separate, single-column documents.

    python -m domains.cos.career_kit.workbook INPUT.json OUTPUT.docx

It never overwrites: if OUTPUT exists, it refuses (pass a new versioned name). Input format: see example_input.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

SCHEMA = "career-narrative-workbook/1"
HEADERS = ("Job", "1. Current text (LinkedIn / resume)", "2. Owner notes", "3. Rewrite (AI v0)", "4. Owner final")
WIDTHS = (0.14, 0.28, 0.20, 0.20, 0.18)                    # of the usable page width
RULES = ("Writing rules: change line first, when there was a change · situation, then operating style, then proof · "
         "concrete nouns, not \"transformation\" · LinkedIn text is the entry line; resume adds 2–3 bullets · wins on "
         "the page, the rest as speaking points · nothing confidential unless the owner clears it · owner's voice wins.")


class InputInvalid(ValueError):
    pass


def _text(value, field):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise InputInvalid(f"{field} must be text")
    return value.strip()


def load(path: Path) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise InputInvalid(f"schema must be {SCHEMA!r}")
    rows = doc.get("rows")
    if not isinstance(rows, list) or not rows:
        raise InputInvalid("rows must be a non-empty list")
    ids = set()
    for i, row in enumerate(rows):
        if not isinstance(row, dict) or row.get("kind") not in ("job", "project", "about"):
            raise InputInvalid(f"rows[{i}].kind must be job, project or about")
        rid = _text(row.get("id"), f"rows[{i}].id")
        if not rid or rid in ids:
            raise InputInvalid(f"rows[{i}].id missing or duplicate")
        ids.add(rid)
        bullets = row.get("resume_bullets", [])
        if not isinstance(bullets, list) or not all(isinstance(b, str) for b in bullets):
            raise InputInvalid(f"rows[{i}].resume_bullets must be a list of text")
    if rows[-1]["kind"] != "about" or sum(r["kind"] == "about" for r in rows) != 1:
        raise InputInvalid("exactly one about row, and it comes last")
    for i, item in enumerate(doc.get("agreement", [])):
        if not isinstance(item, dict) or not _text(item.get("row"), f"agreement[{i}].row"):
            raise InputInvalid(f"agreement[{i}] needs a row")
    return doc


def _shade(cell, hex_fill):
    pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    pr.append(shd)


def _repeat_header(row):
    pr = row._tr.get_or_add_trPr()
    el = OxmlElement("w:tblHeader")
    el.set(qn("w:val"), "true")
    pr.append(el)


def _para(cell, text, *, bold=False, size=9, label=None, first=False):
    p = cell.paragraphs[0] if first else cell.add_paragraph()
    p.paragraph_format.space_after = Pt(2)
    if label:
        r = p.add_run(label + " ")
        r.bold, r.font.size = True, Pt(size)
    r = p.add_run(text)
    r.bold, r.font.size = bold, Pt(size)
    return p


def _table(document, headers, widths, usable):
    table = document.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    for i, col in enumerate(table.columns):            # the grid, which viewers other than Word read
        col.width = Inches(usable * widths[i])
    for i, h in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.width = Inches(usable * widths[i])
        _para(cell, h, bold=True, first=True)
        _shade(cell, "D9E2F3")
    _repeat_header(table.rows[0])
    return table


def _row(table, widths, usable):
    cells = table.add_row().cells
    for i, c in enumerate(cells):
        c.width = Inches(usable * widths[i])
    return cells


def _job_cell(cell, row):
    kind = {"job": "", "project": "Project · ", "about": ""}[row["kind"]]
    if row["kind"] == "about":
        _para(cell, "About / summary, headline (write last)", bold=True, first=True)
        return
    _para(cell, kind + _text(row.get("title"), "title"), bold=True, first=True)
    for field in ("employer", "dates", "place"):
        value = _text(row.get(field), field)
        if value:
            _para(cell, value)
    _para(cell, f"[{row['id']}]", size=7)


def _current_cell(cell, row):
    linkedin = _text(row.get("linkedin"), "linkedin") or "(not on LinkedIn)"
    _para(cell, linkedin, label="LinkedIn:", first=True)
    entry = _text(row.get("resume_entry"), "resume_entry")
    if entry or row.get("resume_bullets"):
        _para(cell, entry, label="Resume:")
        for b in row.get("resume_bullets", []):
            _para(cell, "• " + b.strip())
    else:
        _para(cell, "(not on the resume)", label="Resume:")


def build(doc: dict, out: Path) -> Path:
    if out.exists():
        raise FileExistsError(f"{out} exists; write a new versioned name instead")
    document = Document()
    section = document.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width, section.page_height = Inches(11), Inches(8.5)
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(section, side, Inches(0.5))
    usable = 10.0
    style = document.styles["Normal"]
    style.font.name, style.font.size = "Calibri", Pt(9)

    document.add_heading(_text(doc.get("title"), "title") or "Career narrative workbook", level=1)
    meta = " · ".join(x for x in (_text(doc.get("variant"), "variant") and f"Variant: {doc['variant']}",
                                  _text(doc.get("version"), "version") and f"Version: {doc['version']}") if x)
    if meta:
        document.add_paragraph(meta)
    rules = document.add_paragraph(RULES)
    rules.runs[0].font.size = Pt(8)
    rules.runs[0].font.color.rgb = RGBColor(0x55, 0x55, 0x55)
    document.add_paragraph("Order of work: most recent job first; the About/summary row last. Fill column 2 before "
                           "anything is written in column 3. Column 4 is what ships.").runs[0].font.size = Pt(8)

    table = _table(document, HEADERS, WIDTHS, usable)
    for row in doc["rows"]:
        cells = _row(table, WIDTHS, usable)
        _job_cell(cells[0], row)
        _current_cell(cells[1], row)

    document.add_heading("Speaking points (off the page)", level=2)
    sp = _table(document, ("Job", "Details kept for interviews, not for the resume"), (0.25, 0.75), usable)
    for row in doc["rows"]:
        if row["kind"] != "about":
            cells = _row(sp, (0.25, 0.75), usable)
            _para(cells[0], f"{_text(row.get('employer'), 'employer') or _text(row.get('title'), 'title')} [{row['id']}]",
                  first=True)

    document.add_heading("Agreement check (LinkedIn vs resume)", level=2)
    ac = _table(document, ("Job", "LinkedIn", "Resume", "Decision"), (0.18, 0.32, 0.32, 0.18), usable)
    for item in doc.get("agreement", []):
        cells = _row(ac, (0.18, 0.32, 0.32, 0.18), usable)
        _para(cells[0], _text(item.get("row"), "row"), first=True)
        _para(cells[1], _text(item.get("linkedin"), "linkedin"), first=True)
        _para(cells[2], _text(item.get("resume"), "resume"), first=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    document.save(out)
    return out


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[-3].strip(), file=sys.stderr)
        return 2
    try:
        print(build(load(Path(argv[0])), Path(argv[1])))
    except (InputInvalid, FileExistsError, json.JSONDecodeError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
