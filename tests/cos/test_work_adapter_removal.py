"""The adapter is a way in, not part of what the record says happened.

Mini-moi owns the durable contract; the thing that carried the request is
replaceable. That is only true if removing or renaming the adapter changes no
stored byte and leaves every record readable — which is what this module
checks, by deleting it and reading the tree back without it.
"""

from __future__ import annotations

import importlib
import json
import shutil
import sys
from pathlib import Path

import pytest

from conftest import tree_snapshot
from domains.cos.work import adapter, records, store


def build_full_item(flow):
    """A work item with a source, two revisions and a recorded decision."""
    work_id = flow.started(label="A complete item")
    flow.attach_inline(work_id, "Supplied material.\n")
    written = flow.write(work_id, "A machine draft.\n")
    edited = flow.edit_inline(
        work_id, "A machine draft, corrected.\n", written["result"]["artifact_ref"],
        written["result"]["sha256"],
    )
    proposed = flow.propose(work_id, "approved_text", edited["result"]["artifact_ref"])
    flow.decide(work_id, proposed["result"]["pending_id"], "approved_text")
    return work_id


def describe(record):
    return {
        "work_id": record.work_id,
        "state": record.state,
        "sources": [(s.ref, s.path, s.sha256, s.context_class) for s in record.sources],
        "artifacts": [
            (a.ref, a.path, a.sha256, a.context_class, a.revision, a.supersedes_ref)
            for a in record.artifacts
        ],
        "disposition": (
            record.disposition.state,
            record.disposition.artifact_ref,
        ),
    }


def test_records_survive_adapter_deletion(flow, monkeypatch):
    """with the adapter unimportable, the records read back identically"""
    work_id = build_full_item(flow)
    paths = store.WorkPaths(directory=flow.work_dir(work_id))
    before = describe(records.load_work_record(paths.record))
    tree_before = tree_snapshot(flow.service.store.subject_paths(flow.subject).base)

    module_name = "domains.cos.work.adapter"
    monkeypatch.delitem(sys.modules, module_name, raising=False)
    real_import = importlib.import_module

    def refuse(name, package=None):
        if name.endswith("adapter"):
            raise ImportError("the adapter is not here")
        return real_import(name, package)

    monkeypatch.setattr(importlib, "import_module", refuse)
    with pytest.raises(ImportError):
        importlib.import_module(module_name)

    after = describe(records.load_work_record(paths.record))
    assert after == before
    assert tree_snapshot(flow.service.store.subject_paths(flow.subject).base) == tree_before

    items, issues = flow.service.accumulation.approved_artifacts(flow.subject)
    assert issues == ()
    assert len(items) == 1


def test_records_survive_adapter_rename(flow, tmp_path, monkeypatch):
    """the same records drive fine through a differently named adapter"""
    work_id = build_full_item(flow)
    paths = store.WorkPaths(directory=flow.work_dir(work_id))
    before = describe(records.load_work_record(paths.record))

    source = Path(adapter.__file__).read_text("utf-8")
    renamed = tmp_path / "some_other_entry_point.py"
    renamed.write_text(
        source.replace("InProcessWorkAdapter", "SomeOtherEntryPoint")
        .replace("from .envelope import", "from domains.cos.work.envelope import")
        .replace("from .service import", "from domains.cos.work.service import")
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    module = importlib.import_module("some_other_entry_point")

    driver = module.SomeOtherEntryPoint(flow.service)
    grant = flow.mint("open_work", work_id=work_id)
    response = driver.call(
        "open_work",
        {"subject": flow.subject, "work_id": work_id, "conversation_id": flow.conversation_id},
        grant_ref=grant.grant_ref,
    )
    assert response["ok"] is True
    assert describe(records.load_work_record(paths.record)) == before


def test_no_record_field_names_an_adapter(flow):
    """nothing in a stored record refers to what carried the request"""
    work_id = build_full_item(flow)
    paths = store.WorkPaths(directory=flow.work_dir(work_id))
    document = json.loads(paths.record.read_text("utf-8"))
    rendered = json.dumps(document).casefold()
    for word in ("adapter", "runtime", "provider", "model", "channel", "transport"):
        assert word not in rendered

    assert "adapter_binding" in records.FORBIDDEN_FIELDS
    document["adapter_binding"] = {"name": "in-process"}
    with pytest.raises(records.RecordInvalid) as excinfo:
        records.parse_work_record(document)
    assert "adapter_binding" in excinfo.value.message


def test_adapter_appears_in_no_operation_record(flow):
    """the operation records name identifiers and digests, and nothing else"""
    work_id = build_full_item(flow)
    paths = store.WorkPaths(directory=flow.work_dir(work_id))
    for path in paths.operations.rglob("*"):
        if not path.is_file():
            continue
        rendered = path.read_text("utf-8").casefold()
        for word in ("adapter", "inprocess", "transport"):
            assert word not in rendered
