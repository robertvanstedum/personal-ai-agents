"""Career Narrative Kit workbook generator: layout, empty owner columns, About last, never overwrites."""
import json
from pathlib import Path

import pytest

pytest.importorskip("docx")                       # python-docx: an authoring tool, not a service dependency
from docx import Document  # noqa: E402

from domains.cos.career_kit import workbook  # noqa: E402

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
