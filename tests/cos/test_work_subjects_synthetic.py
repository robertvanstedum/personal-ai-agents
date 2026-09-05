"""Two subjects, one code path, no branch between them.

The first subject is the one whose real needs shaped this. The second is a
different kind of work with different roots and different guidance, and it
runs through the same service without a line of code that knows about either
of them. If a subject ever needed its own branch, this is where that would
show up.

All content is synthetic: invented people, employers and postings.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from domains.cos.work import records, store


def paths_for(flow, work_id):
    return store.WorkPaths(directory=flow.work_dir(work_id))


def test_career_flow_end_to_end(flow):
    """posting and authored material to a draft, an edit, and an approval"""
    work_id = flow.started(
        label="Quayside Logistics operations lead",
        intent="Show a reconciliation gap I actually closed.",
    )

    found = flow.search(work_id, "reconciliation")
    assert found["ok"] is True
    assert {hit["root_ref"] for hit in found["result"]["hits"]} >= {"postings", "authored"}

    posting = flow.attach_file(work_id, "postings", "operations-lead.txt")
    authored = flow.attach_file(work_id, "authored", "current-resume.md")
    assert posting["result"]["context_class"] == "external_source"
    assert authored["result"]["context_class"] == "robert_source"

    read_back = flow.read_captured(work_id, posting["result"]["source_ref"])
    assert "Quayside Logistics" in read_back["result"]["content"]

    draft = flow.write(
        work_id,
        "Dear hiring team,\n\nI closed the third-depot reconciliation gap.\n",
        based_on=[
            {"ref": posting["result"]["source_ref"], "sha256": posting["result"]["sha256"]},
            {"ref": authored["result"]["source_ref"], "sha256": authored["result"]["sha256"]},
        ],
    )
    assert draft["result"]["context_class"] == "agent_draft"

    edited = flow.edit_inline(
        work_id,
        "Dear hiring team,\n\nI rebuilt the ledger and closed the gap in eleven days.\n",
        draft["result"]["artifact_ref"],
        draft["result"]["sha256"],
    )
    assert edited["result"]["context_class"] == "coauthored_output"

    proposed = flow.propose(work_id, "approved_text", edited["result"]["artifact_ref"])
    confirmed = flow.decide(work_id, proposed["result"]["pending_id"], "approved_text")
    assert confirmed["ok"] is True

    items, issues = flow.service.accumulation.approved_artifacts(flow.subject)
    assert issues == ()
    assert [item.sha256 for item in items] == [edited["result"]["sha256"]]
    assert items[0].context_class == "coauthored_output"

    record = records.load_work_record(paths_for(flow, work_id).record)
    assert record.state == "approved_text"
    assert [entry.context_class for entry in record.sources] == [
        "external_source",
        "robert_source",
    ]


def test_decision_memo_flow_end_to_end(memo_flow):
    """a second subject, different roots, and the same code path"""
    flow = memo_flow
    work_id = flow.started(
        label="Vendor selection for the depot scanners",
        intent="Decide between two vendors and record why.",
    )
    assert set(flow.service.accumulation.available_root_refs("decision-memo")) == {
        "memo-notes",
        "approved:decision-memo",
    }

    found = flow.search(work_id, "vendor")
    assert found["ok"] is True
    assert {hit["root_ref"] for hit in found["result"]["hits"]} == {"memo-notes"}

    notes = flow.attach_file(work_id, "memo-notes", "vendor-notes.txt")
    assert notes["result"]["context_class"] == "external_source"

    memo = flow.write(
        work_id,
        "# Recommendation\n\nVendor A, on price for identical scope.\n",
        based_on=[{"ref": notes["result"]["source_ref"], "sha256": notes["result"]["sha256"]}],
    )
    assert memo["result"]["context_class"] == "agent_draft"

    proposed = flow.propose(work_id, "closed")
    confirmed = flow.decide(
        work_id,
        proposed["result"]["pending_id"],
        "closed",
        reason="Procurement paused the programme; no memo is going out.",
    )
    assert confirmed["ok"] is True

    record = records.load_work_record(paths_for(flow, work_id).record)
    assert record.state == "closed"
    assert record.disposition.artifact_ref is None
    assert record.approved_artifact_ref is None


def test_the_two_subjects_do_not_see_each_other(flow, memo_flow):
    """subjects are separate trees, and neither can reach the other's roots"""
    career_id = flow.started(label="An item")
    memo_id = memo_flow.started(label="A memo")

    # the other subject's root cannot even be minted into an authority
    from domains.cos.work.envelope import WorkError

    with pytest.raises(WorkError) as excinfo:
        flow.mint("search_sources", work_id=career_id, root_refs=["memo-notes"])
    assert excinfo.value.code == "source_root_unavailable"

    root = flow.service.store.root
    assert (root / "subjects" / "career").is_dir()
    assert (root / "subjects" / "decision-memo").is_dir()
    assert flow.work_dir(memo_id) is None
    assert memo_flow.work_dir(career_id) is None


def test_no_subject_branch_in_the_package():
    """the service is the same code for both, with no subject in it"""
    import domains.cos.work as package

    root = Path(package.__file__).parent
    for name in ("service.py", "store.py", "grants.py", "approval.py", "adapter.py"):
        text = (root / name).read_text("utf-8").casefold()
        for subject in ("career", "decision-memo", "decision_memo"):
            assert subject not in text
