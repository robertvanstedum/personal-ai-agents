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


def test_the_import_keeps_the_original_and_refuses_completed_import(writer, memory_file):
    memory.import_file(writer, memory_file)
    (original,) = (writer.root / "imports").glob("cos_memory.md.*.orig")
    assert original.read_text() == FILE
    assert memory.imported(writer)
    again = memory.import_file(writer, memory_file)
    assert again["accepted"] == 0 and again["stopped"]["code"] == "memory_import_already_complete"
    assert len(writer.rows("cos.fact")) == 4


def test_interrupted_import_resumes_after_restart_at_a_later_time(writer, memory_file, monkeypatch):
    monkeypatch.setattr(memory, "_now", lambda: "2026-10-31T10:00:00+00:00")
    original_submit = writer.submit
    calls = 0

    def interrupted(command):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("synthetic interruption")
        return original_submit(command)

    monkeypatch.setattr(writer, "submit", interrupted)
    with pytest.raises(OSError):
        memory.import_file(writer, memory_file)
    assert not memory.imported(writer)
    assert len(writer.rows("cos.fact")) == 2
    monkeypatch.setattr(memory, "_now", lambda: "2026-11-01T11:00:00+00:00")
    restarted = StoreWriter(writer.root, ENV)
    result = memory.import_file(restarted, memory_file)
    assert result["accepted"] == 2 and result["replayed"] == 2
    assert len(restarted.rows("cos.fact")) == 4
    assert restarted.rows("cos.fact")[0]["at"] == "2026-10-31T10:00:00+00:00"


def test_incomplete_import_cannot_resume_with_different_source(writer, memory_file, monkeypatch):
    original_submit = writer.submit
    monkeypatch.setattr(writer, "submit", lambda command: {"outcome": "refused", "code": "unavailable"})
    assert "stopped" in memory.import_file(writer, memory_file)
    memory_file.write_text(FILE + "\nChanged source\n")
    monkeypatch.setattr(writer, "submit", original_submit)
    result = memory.import_file(writer, memory_file)
    assert result["stopped"]["code"] == "memory_import_source_changed"
    assert writer.rows("cos.fact") == []


def test_projection_is_refused_even_without_done_marker(writer, memory_file):
    memory.import_file(writer, memory_file)
    memory.write_projection(writer, memory_file)
    (writer.root / memory.DONE_MARKER).unlink()
    result = memory.import_file(writer, memory_file)
    assert result["stopped"]["code"] == "memory_projection_not_importable"
    assert len(writer.rows("cos.fact")) == 4


def test_repeated_model_memory_same_day_is_saved_and_repairs_projection(writer, memory_file, monkeypatch):
    memory.import_file(writer, memory_file)
    original_project = memory.write_projection
    monkeypatch.setattr(memory, "write_projection", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        memory.remember(writer, memory_file, "A synthetic preference", kind="memory_tool",
                        clock=lambda: "2026-10-31T10:00:00+00:00")
    monkeypatch.setattr(memory, "write_projection", original_project)
    result = memory.remember(writer, memory_file, "A synthetic preference", kind="memory_tool",
                             clock=lambda: "2026-10-31T11:00:00+00:00")
    assert result == {"saved": True, "deduplicated": True}
    assert len(writer.rows("cos.fact")) == 5
    assert "A synthetic preference" in memory_file.read_text()


def test_note_operation_reuse_with_changed_text_is_refused(writer, memory_file):
    memory.import_file(writer, memory_file)
    assert memory.remember(writer, memory_file, "Original", kind="explicit_note", operation_id=OP)["saved"]
    result = memory.remember(writer, memory_file, "Different", kind="explicit_note", operation_id=OP)
    assert not result["saved"] and result["code"] == "command_id_reused"


def test_cli_repeat_after_projection_fails_without_importing_projection(tmp_path, memory_file, capsys):
    args = ["--store", str(tmp_path / "store"), "--environment", ENV, "--memory", str(memory_file), "--project"]
    assert memory.main(args) == 0
    capsys.readouterr()
    before = memory_file.read_bytes()
    assert memory.main(args) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["stopped"]["code"] == "memory_import_already_complete"
    assert memory_file.read_bytes() == before


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


@pytest.mark.parametrize("interrupt", [False, True])
def test_cli_projection_recovery_after_completed_import(tmp_path, memory_file, monkeypatch, capsys, interrupt):
    args = ["--store", str(tmp_path / "store"), "--environment", ENV, "--memory", str(memory_file)]
    project = memory.write_projection
    if interrupt:
        def broken_projection(*args):
            memory_file.write_text("")
            raise OSError("interrupted")
        monkeypatch.setattr(memory, "write_projection", broken_projection)
        with pytest.raises(OSError):
            memory.main(args + ["--project"])
        monkeypatch.setattr(memory, "write_projection", project)
    else:
        assert memory.main(args) == 0
    capsys.readouterr()
    assert memory.main(args + ["--project-only"]) == 0
    assert json.loads(capsys.readouterr().out)["projected"] is True
    assert memory_file.read_text().startswith(memory.PROJECTION_HEADER)
    assert len(StoreWriter(tmp_path / "store", ENV).rows("cos.fact")) == 4


def test_cli_projection_repair_requires_completed_import(tmp_path, memory_file, capsys):
    before = memory_file.read_bytes()
    assert memory.main(["--store", str(tmp_path / "store"), "--environment", ENV,
                        "--memory", str(memory_file), "--project-only"]) == 1
    assert json.loads(capsys.readouterr().out)["stopped"]["code"] == "memory_import_not_complete"
    assert memory_file.read_bytes() == before
