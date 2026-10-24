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


# ── hardening found by the end-to-end dry run (24 Oct) ────────────────────────────────────────────────────────

def _fill(path, rid_col_lines, decisions=None):
    from domains.cos.career_kit.export import row_id
    d = Document(path)
    for row in d.tables[0].rows[1:]:
        rid = row_id(row.cells[0])
        for col, lines in rid_col_lines.get(rid, {}).items():
            cell = row.cells[col]
            for i, (text, style) in enumerate(lines):
                para = cell.paragraphs[0] if i == 0 and not cell.paragraphs[0].text else cell.add_paragraph()
                para.add_run(text)
                if style:
                    para.style = d.styles[style]
    for row in d.tables[2].rows[1:]:
        if decisions and row.cells[0].text.strip() in decisions:
            row.cells[3].paragraphs[0].add_run(decisions[row.cells[0].text.strip()])
    d.save(path)


def test_word_list_bullets_and_notes_and_the_draft_preview(tmp_path):
    from domains.cos.career_kit import export
    wb = workbook.build(workbook.load(EXAMPLE), tmp_path / "wb.docx")
    _fill(wb, {"acme": {3: [("Note: helper's note, never exported", None), ("Rewrite entry.", None),
                            ("A bullet made with Word's list button.", "List Bullet")],
                        4: [("Final entry.", None), ("Final bullet via Word list.", "List Bullet")]}})
    final = export.export(EXAMPLE, wb, tmp_path / "final", pages=False)
    texts = [(p.style.name, p.text) for p in Document(final / "resume.docx").paragraphs]
    assert ("List Bullet", "Final bullet via Word list.") in texts
    assert not any("Rewrite entry." in t for _, t in texts)                    # column 3 never ships as final
    draft = export.export(EXAMPLE, wb, tmp_path / "draft", draft=True, pages=False)
    dtexts = [p.text for p in Document(draft / "resume.docx").paragraphs]
    assert any(t.startswith("DRAFT PREVIEW") for t in dtexts)
    assert "Rewrite entry." in dtexts and not any("helper's note" in t for t in dtexts)
    assert "DRAFT (column 3)" in (draft / "report.md").read_text()


def test_carry_forward_keeps_owner_text_and_refuses_to_lose_a_row(tmp_path):
    from domains.cos.career_kit import export
    doc = workbook.load(EXAMPLE)
    old = workbook.build(doc, tmp_path / "v1.docx")
    _fill(old, {"acme": {2: [("my notes", None)], 4: [("my final", None), ("my bullet", "List Bullet")]}},
          {"Beta Telecom": "keep it off LinkedIn"})
    new = workbook.build(doc, tmp_path / "v2.docx", carry_from=old)
    for col in (2, 3, 4):
        assert export.read_finals(old, col) == export.read_finals(new, col)
    assert export.read_decisions(new) == {"Beta Telecom": "keep it off LinkedIn"}
    doc["rows"] = [r for r in doc["rows"] if r["id"] != "acme"]
    with pytest.raises(workbook.InputInvalid):
        workbook.build(doc, tmp_path / "v3.docx", carry_from=old)
    assert not (tmp_path / "v3.docx").exists()


def test_the_export_writes_into_the_owners_resume_keeping_header_and_tail(tmp_path):
    from domains.cos.career_kit import export
    template = _resume(tmp_path / "resume.docx")
    wb = workbook.build(workbook.load(EXAMPLE), tmp_path / "wb.docx")
    _fill(wb, {"acme": {4: [("New entry line.", None), ("New bullet.", "List Bullet")]},
               "about": {4: [("Summary: New summary.", None)]}})
    out = export.export(EXAMPLE, wb, tmp_path / "out", template=template, pages=False)
    texts = [p.text for p in Document(out / "resume.docx").paragraphs]
    assert texts[:2] == ["Example Person", "Springfield · example@example.org"]          # header kept verbatim
    assert "New summary." in texts and "New entry line." in texts and "New bullet." in texts
    assert "Led operations through a merger." not in texts                                 # old middle replaced
    assert texts[-4:] == ["EDUCATION", "B.A., Example University", "CAPABILITIES", "Delivery and operations"]
    assert Document(template).paragraphs[3].text == "Operations leader for platforms in change."  # template untouched


# ── Codex review of PR #369 (24 Oct): two reproduced defects ──────────────────────────────────────────────────

def test_a_row_id_the_input_accepts_is_one_the_export_reads(tmp_path):
    from domains.cos.career_kit import export
    doc = json.loads(EXAMPLE.read_text())
    doc["rows"][0]["id"] = "Acme_1"
    path = tmp_path / "in.json"
    path.write_text(json.dumps(doc))
    wb = workbook.build(workbook.load(path), tmp_path / "wb.docx")
    _fill(wb, {"Acme_1": {4: [("Final for Acme_1.", None)]}})
    out = export.export(path, wb, tmp_path / "out", pages=False)
    assert "Final for Acme_1." in [p.text for p in Document(out / "resume.docx").paragraphs]
    assert "- Acme_1" not in (out / "report.md").read_text()
    doc["rows"][0]["id"] = "bad id [x]"                                 # ids the workbook could not print back
    path.write_text(json.dumps(doc))
    with pytest.raises(workbook.InputInvalid):
        workbook.load(path)


def test_carry_forward_keeps_every_owner_edit_in_speaking_points_and_agreement(tmp_path):
    doc = workbook.load(EXAMPLE)
    old = workbook.build(doc, tmp_path / "v1.docx")
    d = Document(old)
    speaking, agreement = d.tables[1], d.tables[2]
    speaking.rows[1].cells[1].paragraphs[0].add_run("my speaking point")
    speaking.rows[1].cells[1].add_paragraph("second paragraph")
    row = agreement.rows[1]
    row.cells[1].paragraphs[0].add_run(" (owner corrected)")
    row.cells[3].paragraphs[0].add_run("decided")
    added = agreement.add_row().cells                                    # a row the owner added himself
    for cell, text in zip(added, ("Owner-added row", "LI", "Resume", "Decision too")):
        cell.paragraphs[0].add_run(text)
    d.save(old)
    new = workbook.build(doc, tmp_path / "v2.docx", carry_from=old)
    n = Document(new)
    assert n.tables[1].rows[1].cells[1].text == "my speaking point\nsecond paragraph"
    rows = {r.cells[0].text.strip(): [c.text.strip() for c in r.cells] for r in n.tables[2].rows[1:]}
    assert rows["Beta Telecom"][1].endswith("(owner corrected)") and rows["Beta Telecom"][3] == "decided"
    assert rows["Owner-added row"] == ["Owner-added row", "LI", "Resume", "Decision too"]
    doc2 = workbook.load(EXAMPLE)                                         # a speaking point whose row disappears
    doc2["rows"] = [r for r in doc2["rows"] if r["id"] != "acme"]
    with pytest.raises(workbook.InputInvalid):
        workbook.build(doc2, tmp_path / "v3.docx", carry_from=old)
