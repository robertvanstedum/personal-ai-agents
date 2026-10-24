"""Career Narrative Kit workbook generator: layout, empty owner columns, About last, never overwrites."""
import json
from pathlib import Path

import pytest
from docx import Document

from domains.cos.career_kit import workbook

EXAMPLE = Path(workbook.__file__).with_name("example_input.json")


def test_example_builds_a_landscape_workbook_with_owner_columns_empty(tmp_path):
    out = workbook.build(workbook.load(EXAMPLE), tmp_path / "wb.docx")
    doc = Document(out)
    section = doc.sections[0]
    assert section.page_width > section.page_height
    main, speaking, agreement = doc.tables
    assert [c.text for c in main.rows[0].cells] == list(workbook.HEADERS)
    body = main.rows[1:]
    assert len(body) == 4 and "About" in body[-1].cells[0].text
    assert all(not row.cells[i].text for row in body for i in (2, 3, 4))      # owner notes, rewrite, final: empty
    assert "Moved two million accounts" in body[0].cells[1].text
    assert "(not on LinkedIn)" in body[1].cells[1].text
    assert len(speaking.rows) == 1 + 3 and len(agreement.rows) == 2


def test_it_never_overwrites(tmp_path):
    out = tmp_path / "wb.docx"
    out.write_bytes(b"owner edits")
    with pytest.raises(FileExistsError):
        workbook.build(workbook.load(EXAMPLE), out)
    assert out.read_bytes() == b"owner edits"
    assert workbook.main([str(EXAMPLE), str(out)]) == 1


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(schema="other"),
    lambda d: d["rows"].append(dict(d["rows"][0])),                 # duplicate id, about not last
    lambda d: d["rows"].pop(),                                      # no about row
    lambda d: d["rows"][0].update(resume_bullets="one string"),
])
def test_invalid_input_is_refused(tmp_path, mutate):
    doc = json.loads(EXAMPLE.read_text())
    mutate(doc)
    path = tmp_path / "in.json"
    path.write_text(json.dumps(doc))
    with pytest.raises(workbook.InputInvalid):
        workbook.load(path)


# ── the flow after the workbook: import a resume, export the owner's finals, assemble a letter ──────────────

def _resume(path):
    d = Document()
    for text, style in [("Example Person", None), ("Springfield · example@example.org", None),
                        ("PROFESSIONAL SUMMARY", None), ("Operations leader for platforms in change.", None),
                        ("CURRENT WORK", None), ("Side project   |   example.org", None), ("2024–Present", None),
                        ("A personal project in daily use.", None),
                        ("PROFESSIONAL EXPERIENCE", None), ("Director  |   Acme Networks   Springfield", None),
                        ("2020–Present", None), ("Led operations through a merger.", None),
                        ("Cut incidents by half.", "List Paragraph"),
                        ("EDUCATION", None), ("B.A., Example University", None),
                        ("CAPABILITIES", None), ("Delivery and operations", None)]:
        d.add_paragraph(text, style=style) if style else d.add_paragraph(text)
    d.save(path)
    return path


def test_resume_import_splits_roles_and_keeps_header_and_appendix(tmp_path):
    from domains.cos.career_kit import resume_import
    path = _resume(tmp_path / "r.docx")
    rows = resume_import.read(path)
    assert [r["kind"] for r in rows] == ["job", "project", "about"]
    job = rows[0]
    assert (job["title"], job["employer"], job["place"], job["dates"]) == ("Director", "Acme Networks", "Springfield",
                                                                          "2020–Present")
    assert job["resume_bullets"] == ["Cut incidents by half."] and "merger" in job["resume_entry"]
    header, appendix = resume_import.read_frame(path)
    assert header == ["Example Person", "Springfield · example@example.org"]
    assert [a["text"] for a in appendix][:2] == ["EDUCATION", "B.A., Example University"]


def test_export_uses_only_owner_finals_and_reads_decisions(tmp_path):
    from domains.cos.career_kit import export
    doc = workbook.load(EXAMPLE)
    wb = workbook.build(doc, tmp_path / "wb.docx")
    edited = Document(wb)
    main, _, agreement = edited.tables
    acme = main.rows[1].cells[4]
    acme.paragraphs[0].add_run("LinkedIn: Ran platform operations through the Acme–Beta merger.")
    acme.add_paragraph("Led platform operations through a merger and a billing cut-over.")
    acme.add_paragraph("• Moved two million accounts with no customer impact.")
    about = main.rows[-1].cells[4]
    about.paragraphs[0].add_run("Headline: Operations leader")
    about.add_paragraph("Summary: Operations leader for platforms in change.")
    agreement.rows[1].cells[3].paragraphs[0].add_run("Add Beta Telecom to LinkedIn")
    edited.save(wb)
    out = export.export(EXAMPLE, wb, tmp_path / "out")
    resume = Document(out / "resume.docx")
    text = [p.text for p in resume.paragraphs]
    assert not resume.tables
    assert "Moved two million accounts with no customer impact." in text
    assert "Program Manager  |  Beta Telecom   Shelbyville" not in text          # no owner final: not exported
    report = (out / "report.md").read_text()
    assert "- beta" in report and "decision: Add Beta Telecom to LinkedIn" in report
    assert "Ran platform operations" in (out / "linkedin.md").read_text()
    with pytest.raises(FileExistsError):
        export.export(EXAMPLE, wb, tmp_path / "out")


def test_letter_refuses_a_placeholder_opening_and_never_overwrites(tmp_path):
    from domains.cos.career_kit import letter
    parts = {"greeting": "Hello,", "opening": "[P1 from the posting]", "middle": ["Middle."], "close": "Close.",
             "signoff": "Regards,", "name": "Example Person"}
    with pytest.raises(letter.PartsInvalid):
        letter.write(parts, tmp_path / "l.docx")
    parts["opening"] = "You need someone to run releases across teams."
    out = letter.write(parts, tmp_path / "l.docx")
    assert (tmp_path / "l.md").read_text().startswith("Hello,\n\nYou need someone")
    with pytest.raises(FileExistsError):
        letter.write(parts, out)
