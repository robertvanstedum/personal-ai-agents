"""B1a: cos_memory.md moves into the CoS store with its real provenance, then becomes its projection
(spec v0.3 sections 4.2 and 4.5). All content is synthetic."""
from __future__ import annotations

import json
import os

import pytest

from domains.cos.store import StoreWriter, contracts
from domains.cos.store import memory

ENV = "dev"
OP = "6f1c2b9e-1111-4222-8333-944455556666"
FILE = f"""# CoS Memory — distilled
Last distilled: 2026-07-01

## Enduring facts and decisions
[What is always true about Robert's context, preferences, confirmed decisions]
- Prefers short morning check-ins.

## Active agenda
[What CoS is currently working on]

[chat] 2026-07-11: Synthetic chat memory line.
<!-- cos-operation:{OP} -->
[note] 2026-10-20: Call the synthetic dentist before Friday.
[note] 2026-10-21: A note whose marker was lost.
"""


@pytest.fixture
def writer(tmp_path):
    return StoreWriter(tmp_path / "store", ENV)


@pytest.fixture
def memory_file(tmp_path):
    path = tmp_path / "cos_memory.md"
    path.write_text(FILE)
    return path


def test_parse_keeps_sections_tags_and_operations_and_skips_the_template(memory_file):
    entries = memory.parse(memory_file.read_text())
    assert [(e["section"], e["tag"], e["text"]) for e in entries] == [
        ("Enduring facts and decisions", "distilled", "Prefers short morning check-ins."),
        ("Active agenda", "chat", "Synthetic chat memory line."),
        ("Active agenda", "note", "Call the synthetic dentist before Friday."),
        ("Active agenda", "note", "A note whose marker was lost."),
    ]
    assert entries[2]["operation_id"] == OP and "operation_id" not in entries[3]


def test_the_import_keeps_real_provenance_and_never_claims_roberts_words(writer, memory_file):
    counts = memory.import_file(writer, memory_file)
    assert counts == {"entries": 4, "accepted": 4, "replayed": 0, "approved_notes": 1}
    facts = writer.rows("cos.fact")
    assert [f["entry"]["provenance"] for f in facts] == ["agent_draft", "agent_draft", "approved", "agent_draft"]
    assert facts[2]["entry"]["ref"] == {"type": "cos.memory_note", "id": OP}
    assert all(not contracts.errors("store-records", "standing_fact", f) for f in facts)
    assert not any(f["entry"]["provenance"] == "robert_source" for f in facts)


def test_the_import_keeps_the_original_and_is_safe_to_rerun(writer, memory_file):
    memory.import_file(writer, memory_file)
    (original,) = (writer.root / "imports").glob("cos_memory.md.*.orig")
    assert original.read_text() == FILE
    assert memory.imported(writer)
    # Once done, a second import is refused rather than replayed (review M1); an interrupted run still replays,
    # see test_a_rerun_later_replays_even_for_undated_lines.
    with pytest.raises(memory.ImportRefused):
        memory.import_file(writer, memory_file)
    assert len(writer.rows("cos.fact")) == 4


def test_nothing_writes_the_file_before_the_import_is_done(writer, memory_file):
    assert not memory.imported(writer)
    assert memory_file.read_text() == FILE


def test_the_projection_is_rewritten_in_place_so_the_other_container_sees_it(writer, memory_file):
    memory.import_file(writer, memory_file)
    inode = os.stat(memory_file).st_ino
    text = memory.write_projection(writer, memory_file)
    assert os.stat(memory_file).st_ino == inode
    assert text.startswith(memory.PROJECTION_HEADER)
    assert "## Active agenda\n[chat] 2026-07-11: Synthetic chat memory line." in text
    assert "[note] 2026-10-20: Call the synthetic dentist before Friday." in text
    assert "[What is always true" not in text


def test_new_facts_go_to_the_store_and_a_retried_note_is_not_kept_twice(writer, memory_file):
    memory.import_file(writer, memory_file)
    clock = lambda: "2026-10-31T09:00:00+00:00"
    first = memory.remember(writer, memory_file, "Synthetic   new note", kind="explicit_note",
                            operation_id="op-new-1", clock=clock)
    assert first == {"saved": True, "deduplicated": False}
    again = memory.remember(writer, memory_file, "Synthetic new note", kind="explicit_note",
                            operation_id="op-new-1", clock=lambda: "2026-11-02T09:00:00+00:00")
    assert again == {"saved": True, "deduplicated": True}
    model = memory.remember(writer, memory_file, "Model's own memory", kind="memory_tool", clock=clock)
    assert model["saved"] is True
    facts = writer.rows("cos.fact")
    assert [f["entry"]["provenance"] for f in facts[-2:]] == ["approved", "agent_draft"]
    assert "## Recent\n[note] 2026-10-31: Synthetic new note\n[chat] 2026-10-31: Model's own memory" \
        in memory_file.read_text()


def test_over_the_cap_the_oldest_facts_are_left_out_whole_and_counted(writer, memory_file):
    memory.import_file(writer, memory_file)
    for n in range(40):
        memory.remember(writer, memory_file, f"Synthetic fact number {n} " + "x" * 150, kind="memory_tool",
                        clock=lambda n=n: f"2026-10-31T10:{n:02d}:00+00:00")
    text = memory_file.read_text()
    assert len(text) <= memory.MEMORY_CAP
    assert "older facts are kept in the CoS store" in text
    assert "Synthetic fact number 39" in text and "Synthetic chat memory line" not in text
    assert len(writer.rows("cos.fact")) == 44


def test_the_command_line_imports_and_projects(tmp_path, memory_file, capsys):
    code = memory.main(["--store", str(tmp_path / "store"), "--environment", ENV, "--memory", str(memory_file),
                        "--project"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["accepted"] == 4 and out["projected"] is True
    assert memory_file.read_text().startswith(memory.PROJECTION_HEADER)


# ---- review M1 and S2 (#462) ---------------------------------------------------------------------------------------

UNDATED = """## Enduring facts and decisions
- An undated distilled line.
[chat] 2026-07-11: A dated line.
"""


def test_a_rerun_later_replays_even_for_undated_lines(writer, tmp_path, monkeypatch):
    path = tmp_path / "undated.md"
    path.write_text(UNDATED)
    first = memory.import_file(writer, path)
    (writer.root / memory.DONE_MARKER).unlink()           # as if the first run was interrupted before the marker
    monkeypatch.setattr(memory, "_now", lambda: "2031-01-01T00:00:00+00:00")   # a later clock
    again = memory.import_file(writer, path)
    assert first["accepted"] == 2 and again == {"entries": 2, "accepted": 0, "replayed": 2, "approved_notes": 0}
    assert len(writer.rows("cos.fact")) == 2


def test_the_import_refuses_after_it_is_done_or_on_the_projection(writer, memory_file):
    memory.import_file(writer, memory_file)
    with pytest.raises(memory.ImportRefused):
        memory.import_file(writer, memory_file)
    (writer.root / memory.DONE_MARKER).unlink()
    memory.write_projection(writer, memory_file)
    with pytest.raises(memory.ImportRefused):
        memory.import_file(writer, memory_file)
    assert len(writer.rows("cos.fact")) == 4


def test_the_command_line_refuses_a_second_import(tmp_path, memory_file, capsys):
    args = ["--store", str(tmp_path / "store"), "--environment", ENV, "--memory", str(memory_file), "--project"]
    assert memory.main(args) == 0
    capsys.readouterr()
    assert memory.main(args) == 2 and "refused" in capsys.readouterr().out


def test_the_model_saving_the_same_line_twice_in_a_day_is_kept_once(writer, memory_file):
    memory.import_file(writer, memory_file)
    clock = iter(["2026-10-31T09:00:00+00:00", "2026-10-31T15:30:00+00:00"])
    first = memory.remember(writer, memory_file, "Same line", kind="memory_tool", clock=lambda: next(clock))
    again = memory.remember(writer, memory_file, "Same line", kind="memory_tool", clock=lambda: next(clock))
    assert first == {"saved": True, "deduplicated": False} and again == {"saved": True, "deduplicated": True}
