"""Export a finished workbook: column 4 (owner final) becomes the outward-facing texts.

    python -m domains.cos.career_kit.export INPUT.json WORKBOOK.docx OUTDIR

INPUT.json is the generator's input (titles, employers, dates, places, optional "header" lines for the resume top).
WORKBOOK.docx is the owner's edited workbook; rows are matched by the [id] printed in the Job cell. In column 4:
  - a paragraph starting "LinkedIn:" is the LinkedIn entry text for that role;
  - paragraphs starting "•" or "-" are resume bullets;
  - any other paragraph is the resume entry line;
  - in the About row: "Headline:", "About:" (LinkedIn) and "Summary:" (resume) paragraphs.
A row whose column 4 is empty is NOT exported with AI or old text: it is listed in the report as missing.

Writes into OUTDIR (which must not exist): resume.docx (single column, no tables or text boxes, so applicant tracking
systems read it), linkedin.md, and report.md (missing finals, length flags, LinkedIn/resume agreement flags).
Length limits are LinkedIn's published limits at the time of writing; verify them when they matter.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from docx import Document
from docx.shared import Pt

from .workbook import load

LIMITS = {"headline": 220, "about": 2600, "experience": 2000}       # characters; verify when they matter
_ID = re.compile(r"\[([a-z0-9][a-z0-9-]*)\]\s*$")


def read_finals(workbook: Path) -> dict:
    """{row id: [paragraph texts of column 4]}, from the main table."""
    table = Document(workbook).tables[0]
    out = {}
    for row in table.rows[1:]:
        cells = row.cells
        job_lines = [p.text.strip() for p in cells[0].paragraphs if p.text.strip()]
        if job_lines and job_lines[0].startswith("About / summary"):
            rid = "about"
        else:
            m = _ID.search(job_lines[-1]) if job_lines else None
            if not m:
                continue
            rid = m.group(1)
        out[rid] = [p.text.strip() for p in cells[4].paragraphs if p.text.strip()]
    return out


def read_decisions(workbook: Path) -> dict:
    """{agreement row label: the owner's Decision cell}, from the workbook's agreement-check table."""
    tables = Document(workbook).tables
    if len(tables) < 3:
        return {}
    out = {}
    for row in tables[2].rows[1:]:
        cells = row.cells
        label, decision = cells[0].text.strip(), cells[-1].text.strip()
        if label and decision:
            out[label] = decision
    return out


def parse_final(lines: list[str], about: bool) -> dict:
    final = {"entry": [], "bullets": [], "linkedin": [], "headline": "", "about": [], "summary": []}
    for line in lines:
        low = line.lower()
        if about and low.startswith("headline:"):
            final["headline"] = line.split(":", 1)[1].strip()
        elif about and low.startswith("about:"):
            final["about"].append(line.split(":", 1)[1].strip())
        elif about and low.startswith("summary:"):
            final["summary"].append(line.split(":", 1)[1].strip())
        elif low.startswith("linkedin:"):
            final["linkedin"].append(line.split(":", 1)[1].strip())
        elif line[:1] in ("•", "-"):
            final["bullets"].append(line[1:].strip())
        elif about:
            final["summary"].append(line)
        else:
            final["entry"].append(line)
    return final


def export(input_path: Path, workbook: Path, outdir: Path) -> Path:
    if outdir.exists():
        raise FileExistsError(f"{outdir} exists; use a new versioned folder")
    doc = load(input_path)
    finals = read_finals(workbook)
    missing, flags, linkedin_md = [], [], []
    resume = Document()
    style = resume.styles["Normal"]
    style.font.name, style.font.size = "Calibri", Pt(10.5)
    for line in doc.get("header", []):
        resume.add_paragraph(line)
    rows = doc["rows"]
    about = parse_final(finals.get("about", []), about=True)
    if not finals.get("about"):
        missing.append("about")
    if about["summary"]:
        resume.add_paragraph("PROFESSIONAL SUMMARY")
        for s in about["summary"]:
            resume.add_paragraph(s)
    if about["headline"]:
        linkedin_md += ["## Headline", "", about["headline"], ""]
        if len(about["headline"]) > LIMITS["headline"]:
            flags.append(f"headline: {len(about['headline'])} characters > {LIMITS['headline']}")
    if about["about"]:
        text = "\n\n".join(about["about"])
        linkedin_md += ["## About", "", text, ""]
        if len(text) > LIMITS["about"]:
            flags.append(f"about: {len(text)} characters > {LIMITS['about']}")
    for kind, heading in (("project", "CURRENT WORK"), ("job", "PROFESSIONAL EXPERIENCE")):
        group = [r for r in rows if r["kind"] == kind]
        if not group:
            continue
        resume.add_paragraph(heading)
        if kind == "job":
            linkedin_md += ["## Experience", ""]
        for r in group:
            lines = finals.get(r["id"], [])
            if not lines:
                missing.append(r["id"])
                continue
            f = parse_final(lines, about=False)
            place = f"   {r['place']}" if r.get("place") else ""
            resume.add_paragraph(f"{r.get('title', '')}  |  {r.get('employer', '')}{place}")
            if r.get("dates"):
                resume.add_paragraph(r["dates"])
            for e in f["entry"]:
                resume.add_paragraph(e)
            for b in f["bullets"]:
                resume.add_paragraph(b, style="List Bullet")
            if kind == "job" and f["linkedin"]:
                text = " ".join(f["linkedin"])
                linkedin_md += [f"### {r.get('title', '')} · {r.get('employer', '')}", "", text, ""]
                if len(text) > LIMITS["experience"]:
                    flags.append(f"{r['id']}: LinkedIn entry {len(text)} characters > {LIMITS['experience']}")
    for item in doc.get("appendix", []):                      # education, capabilities ...: carried unchanged
        if item.get("bullet"):
            resume.add_paragraph(item["text"], style="List Bullet")
        else:
            resume.add_paragraph(item["text"])
    decisions = read_decisions(workbook)
    agreement = [f"- {a['row']}: LinkedIn \"{a.get('linkedin', '')}\" · resume \"{a.get('resume', '')}\""
                 + (f" · decision: {decisions[a['row']]}" if decisions.get(a["row"]) else " · decision open")
                 for a in doc.get("agreement", [])]
    outdir.mkdir(parents=True)
    resume.save(outdir / "resume.docx")
    (outdir / "linkedin.md").write_text("\n".join(linkedin_md) + "\n", encoding="utf-8")
    report = ["# Export report", "",
              f"Input: {input_path.name} · workbook: {workbook.name}", "",
              "## Rows without an owner final (not exported)", ""] + \
             ([f"- {m}" for m in missing] or ["- none"]) + \
             ["", "## Length flags (LinkedIn)", ""] + ([f"- {x}" for x in flags] or ["- none"]) + \
             ["", "## LinkedIn / resume agreement (review; nothing is auto-corrected)", ""] + (agreement or ["- none"])
    (outdir / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return outdir


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 3:
        print("usage: python -m domains.cos.career_kit.export INPUT.json WORKBOOK.docx OUTDIR", file=sys.stderr)
        return 2
    try:
        print(export(Path(argv[0]), Path(argv[1]), Path(argv[2])))
    except (FileExistsError, ValueError, json.JSONDecodeError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
