"""Nothing that leaves this package carries a body or an absolute path.

Receipts, results, errors, operation records and orientation are all places
a body could leak by accident. This module drives a full flow with
distinctive synthetic text in every free-text field and then looks for any
fragment of it in everything the service produced.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from domains.cos.work import store
from domains.cos.work.envelope import RECEIPT_KEYS, Receipt

BODY = (
    "The reconciliation gap at the third depot stayed open for eleven weeks "
    "before anyone reported it, and closing it took a rebuilt ledger.\n"
)
LABEL = "A label carrying its own unmistakable phrasing for this test only"
INTENT = "An intent carrying a second unmistakable phrase for this same test"
NOTE = "An origin note with a third unmistakable phrase inside it entirely"
REASON = "A reason with a fourth unmistakable phrase that appears nowhere else"

SECRETS = (BODY, LABEL, INTENT, NOTE, REASON)


def fragments(text, size=40):
    return [text[index : index + size] for index in range(0, max(1, len(text) - size), 17)]


def run_flow(flow):
    """One complete flow with a distinctive body in every free-text field."""
    responses = []
    work_id = flow.started(label=LABEL, intent=INTENT)
    responses.append(flow.attach_inline(work_id, BODY, origin_note=NOTE))
    responses.append(flow.write(work_id, BODY))
    proposed = flow.propose(work_id, "closed")
    responses.append(proposed)
    responses.append(
        flow.decide(work_id, proposed["result"]["pending_id"], "closed", reason=REASON)
    )
    responses.append(flow.open_existing(work_id))
    responses.append(flow.search(work_id, "reconciliation"))
    return work_id, responses


def test_no_body_substring_anywhere(flow):
    """no fragment of any body, label, intent, note or reason escapes"""
    work_id, responses = run_flow(flow)
    paths = store.WorkPaths(directory=flow.work_dir(work_id))

    surfaces = []
    for response in responses:
        surfaces.append(json.dumps(response["receipt"]))
        surfaces.append(json.dumps(response["error"]))
    for path in paths.operations.rglob("*"):
        if path.is_file():
            surfaces.append(path.read_text("utf-8"))
    for path in flow.service.store.subject_paths(flow.subject).creates.iterdir():
        surfaces.append(path.read_text("utf-8"))

    for surface in surfaces:
        for secret in SECRETS:
            for fragment in fragments(secret):
                assert fragment not in surface


def test_orientation_carries_no_bodies(flow):
    """orientation names entries, never their contents"""
    work_id, _responses = run_flow(flow)
    opened = flow.open_existing(work_id)
    rendered = json.dumps(opened["result"])
    for fragment in fragments(BODY):
        assert fragment not in rendered
    # the label and intent are the caller's own words about the work item and
    # do belong in orientation; the material does not
    assert opened["result"]["label"] == LABEL
    assert "content" not in rendered


def test_no_absolute_path_anywhere(flow, work_root, synthetic_roots):
    """no root's absolute path appears in anything the service produced"""
    work_id, responses = run_flow(flow)
    paths = store.WorkPaths(directory=flow.work_dir(work_id))
    absolutes = [str(work_root), str(synthetic_roots)]

    surfaces = [json.dumps(response) for response in responses]
    for path in list(paths.operations.rglob("*")) + list(
        flow.service.store.subject_paths(flow.subject).creates.iterdir()
    ):
        if path.is_file():
            surfaces.append(path.read_text("utf-8"))

    for surface in surfaces:
        for absolute in absolutes:
            assert absolute not in surface


def test_error_paths_are_relative(flow):
    """every error's relative_path is work-relative or root-relative"""
    work_id = flow.started()
    attached = flow.attach_inline(work_id, "An input.\n")
    stale = flow.write(
        work_id,
        "A draft.\n",
        based_on=[{"ref": attached["result"]["source_ref"], "sha256": "0" * 64}],
    )
    assert stale["ok"] is False
    assert not stale["error"]["relative_path"].startswith("/")


def test_receipt_keys_are_allowlisted(flow):
    """every receipt the service produces passes the allowlist"""
    _work_id, responses = run_flow(flow)
    for response in responses:
        receipt = response["receipt"]
        assert receipt is not None
        assert set(receipt) - {"operation_id", "effect", "outcome"} <= RECEIPT_KEYS
        rebuilt = Receipt(
            operation_id=receipt["operation_id"],
            effect=receipt["effect"],
            outcome=receipt["outcome"],
            fields={
                key: value
                for key, value in receipt.items()
                if key not in ("operation_id", "effect", "outcome")
            },
        )
        assert rebuilt.as_dict() == receipt
