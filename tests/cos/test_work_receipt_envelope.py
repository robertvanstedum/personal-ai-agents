"""The completed version-1 receipt: exact per effect, and never prose.

A receipt is the only durable statement the system makes about what it did,
so what it may say is a contract rather than a convention. Two properties are
checked here exhaustively: no key outside the allowlist can appear at all,
and each effect's key set is *exactly* the one its row names — a missing key
and an extra key are the same error.
"""

from __future__ import annotations

import itertools

import pytest

from domains.cos.work.envelope import (
    OUTCOMES,
    PROPOSED_STATES,
    RECEIPT_KEYS,
    RECEIPT_SHAPES,
    TERMINAL_OUTCOMES,
    InvalidRequest,
    Receipt,
    WorkError,
    error_response,
    make_receipt,
    success_response,
)
from domains.cos.work.records import WORK_STATES

OPERATION_ID = "6f2b8ea1-4d73-4c59-a018-3be7d2f9c456"
OTHER_ID = "1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f"
DIGEST = "a" * 64

#: The design's per-effect table, restated here as data so the code's table
#: can be compared with it rather than with itself.
DESIGN_TABLE: dict[tuple[str, str], set[str]] = {
    ("open_work", "committed"): {"subject", "work_id", "state", "created_at"},
    ("open_work", "ok"): {"subject", "work_id", "state"},
    ("attach_source", "committed"): {
        "subject", "work_id", "ref", "relative_path", "sha256", "bytes",
        "context_class", "created_at",
    },
    ("search_sources", "ok"): {"subject", "result_count"},
    ("read_source", "ok"): {"subject", "root_ref", "relative_path", "sha256", "bytes"},
    ("write_artifact", "committed"): {
        "subject", "work_id", "ref", "revision", "relative_path", "sha256",
        "bytes", "context_class", "created_at",
    },
    ("request_disposition", "committed"): {
        "subject", "work_id", "pending_id", "proposed_state", "state",
        "expires_at", "created_at",
    },
    ("record_disposition", "committed"): {
        "subject", "work_id", "pending_id", "state", "created_at",
    },
    ("use_robert_edit", "committed"): {
        "subject", "work_id", "ref", "revision", "relative_path", "sha256",
        "bytes", "context_class", "supersedes_ref", "created_at",
    },
}

READ_SOURCE_CAPTURED = {
    "subject", "work_id", "ref", "relative_path", "sha256", "bytes", "context_class",
}

VALUES = {
    "subject": "career",
    "work_id": "3f1c9a2e-7b64-4d5a-9c31-8ea20b45d701",
    "pending_id": "0a5d31c6-9e72-4a18-b3f5-27c69d08e4b1",
    "ref": "art-0002",
    "supersedes_ref": "art-0001",
    "revision": 2,
    "state": "continuing",
    "proposed_state": "closed",
    "context_class": "agent_draft",
    "relative_path": "artifacts/0002-letter.md",
    "root_ref": "authored",
    "sha256": DIGEST,
    "bytes": 562,
    "result_count": 3,
    "created_at": "2026-08-13T17:02:41Z",
    "expires_at": "2026-08-13T17:12:41Z",
}


def build(effect, outcome, keys, **overrides):
    fields = {key: VALUES[key] for key in keys}
    fields.update(overrides)
    return Receipt(operation_id=OPERATION_ID, effect=effect, outcome=outcome, fields=fields)


def test_receipt_direct_construction_is_validated():
    """there is no construction path that skips validation"""
    with pytest.raises(InvalidRequest):
        Receipt(operation_id="not-a-uuid", effect="read_source", outcome="ok", fields={})
    with pytest.raises(InvalidRequest):
        Receipt(operation_id=OPERATION_ID, effect="nonsense", outcome="ok", fields={})
    with pytest.raises(InvalidRequest):
        Receipt(operation_id=OPERATION_ID, effect="read_source", outcome="", fields={})
    with pytest.raises(InvalidRequest):
        Receipt(
            operation_id=OPERATION_ID,
            effect="search_sources",
            outcome="ok",
            fields={"subject": "career", "result_count": 1, "content": "the whole letter"},
        )
    with pytest.raises(InvalidRequest):
        Receipt(operation_id=OPERATION_ID, effect="search_sources", outcome="ok", fields=[])


@pytest.mark.parametrize(
    "name",
    [
        "content",
        "label",
        "intent",
        "origin_note",
        "reason",
        "confirmation_sentence",
        "based_on",
        "filename_hint",
        "path",
        "adapter",
        "absolute_path",
        "retry",
    ],
)
def test_receipt_rejects_unlisted_key(name):
    """no excluded name can be smuggled into a receipt"""
    assert name not in RECEIPT_KEYS
    with pytest.raises(InvalidRequest) as excinfo:
        Receipt(
            operation_id=OPERATION_ID,
            effect="search_sources",
            outcome="ok",
            fields={"subject": "career", "result_count": 1, name: "anything"},
        )
    assert name in excinfo.value.message


@pytest.mark.parametrize(
    "key,value",
    [
        ("sha256", "not-a-digest"),
        ("bytes", -1),
        ("revision", 0),
        ("state", "invented"),
        ("context_class", "invented"),
        ("relative_path", "/etc/passwd"),
        ("relative_path", "../escape.md"),
        ("work_id", "not-a-uuid"),
        ("pending_id", "not-a-uuid"),
        ("subject", "Not An Identifier"),
        ("ref", "artifact-two"),
        ("created_at", "13 August"),
        ("expires_at", "soon"),
        ("proposed_state", "continuing"),
    ],
)
def test_receipt_rejects_malformed_value(key, value):
    """the per-key grammar refuses a malformed value

    A malformed relative path is refused by the same confinement rule the
    rest of the package uses, so it reports ``path_denied`` rather than a
    second opinion about what a bad path is.
    """
    with pytest.raises(WorkError):
        build("use_robert_edit", "committed", DESIGN_TABLE[("use_robert_edit", "committed")],
              **{key: value})


def test_receipt_fields_per_effect_exact():
    """every effect and mode carries exactly the fields its row names"""
    for (effect, outcome), keys in DESIGN_TABLE.items():
        receipt = build(effect, outcome, keys)
        assert set(receipt.fields) == keys
    # the two disposition modes and the second read mode
    approving = build(
        "request_disposition",
        "committed",
        DESIGN_TABLE[("request_disposition", "committed")] | {"ref", "sha256"},
        proposed_state="approved_text",
    )
    assert {"ref", "sha256"} <= set(approving.fields)
    confirmed = build(
        "record_disposition",
        "committed",
        DESIGN_TABLE[("record_disposition", "committed")] | {"ref", "sha256"},
        state="approved_text",
    )
    assert {"ref", "sha256"} <= set(confirmed.fields)
    captured = build("read_source", "ok", READ_SOURCE_CAPTURED)
    assert "root_ref" not in captured.fields


def test_receipt_shape_table_equals_the_design_table():
    """the table in the code and the table in the design are one table"""
    assert set(RECEIPT_SHAPES) == set(DESIGN_TABLE)
    for key, keys in DESIGN_TABLE.items():
        assert set(RECEIPT_SHAPES[key]) == keys


def test_receipt_outcome_set_is_two_values():
    """a receipt says ok or committed, and nothing else"""
    assert OUTCOMES == frozenset({"ok", "committed"})
    assert TERMINAL_OUTCOMES == frozenset({"committed", "abandoned", "quarantined"})
    for outcome in ("duplicate", "abandoned", "quarantined"):
        with pytest.raises(InvalidRequest):
            Receipt(
                operation_id=OPERATION_ID,
                effect="write_artifact",
                outcome=outcome,
                fields={},
            )


def test_retry_flag_is_not_in_the_receipt():
    """retry status is response metadata, never part of the statement"""
    assert "retry" not in RECEIPT_KEYS
    with pytest.raises(InvalidRequest):
        build("open_work", "ok", DESIGN_TABLE[("open_work", "ok")], retry=True)


def test_proposed_state_only_on_request_disposition():
    """the proposed state is a key of exactly one effect"""
    receipt = build(
        "request_disposition", "committed", DESIGN_TABLE[("request_disposition", "committed")]
    )
    assert receipt.fields["proposed_state"] in PROPOSED_STATES
    for effect, outcome in DESIGN_TABLE:
        if effect == "request_disposition":
            continue
        with pytest.raises(InvalidRequest):
            build(effect, outcome, DESIGN_TABLE[(effect, outcome)] | {"proposed_state"})


def test_state_key_means_record_state_everywhere():
    """state is the record's state after the operation, on every effect"""
    for (effect, outcome), keys in DESIGN_TABLE.items():
        if "state" not in keys:
            continue
        for value in sorted(WORK_STATES):
            fields = dict(VALUES)
            receipt = build(effect, outcome, keys, state=value) if (
                effect != "record_disposition" or value != "approved_text"
            ) else build(
                effect, outcome, keys | {"ref", "sha256"}, state=value
            )
            assert receipt.fields["state"] == value
        assert "state" in RECEIPT_SHAPES[(effect, outcome)]


def test_disposition_receipt_fields_are_conditional():
    """approval names the artifact; closing and unresolved name nothing"""
    for state in ("closed", "unresolved"):
        plain = build(
            "record_disposition", "committed",
            DESIGN_TABLE[("record_disposition", "committed")], state=state,
        )
        assert "ref" not in plain.fields and "sha256" not in plain.fields
        with pytest.raises(InvalidRequest):
            build(
                "record_disposition", "committed",
                DESIGN_TABLE[("record_disposition", "committed")] | {"ref", "sha256"},
                state=state,
            )
        with pytest.raises(InvalidRequest):
            build(
                "request_disposition", "committed",
                DESIGN_TABLE[("request_disposition", "committed")] | {"ref", "sha256"},
                proposed_state=state,
            )
    with pytest.raises(InvalidRequest):
        build(
            "record_disposition", "committed",
            DESIGN_TABLE[("record_disposition", "committed")], state="approved_text",
        )
    with pytest.raises(InvalidRequest):
        build(
            "request_disposition", "committed",
            DESIGN_TABLE[("request_disposition", "committed")], proposed_state="approved_text",
        )


@pytest.mark.parametrize(
    "effect,outcome,extra",
    [
        ("search_sources", "ok", "work_id"),
        ("search_sources", "ok", "pending_id"),
        ("attach_source", "committed", "revision"),
        ("write_artifact", "committed", "supersedes_ref"),
        ("read_source", "ok", "state"),
    ],
)
def test_receipt_effect_illegal_key_refused(effect, outcome, extra):
    """a globally legal key this effect may not carry is still refused"""
    with pytest.raises(InvalidRequest):
        build(effect, outcome, DESIGN_TABLE[(effect, outcome)] | {extra})


def test_receipt_missing_required_key_refused():
    """dropping any one required key from any effect raises"""
    for (effect, outcome), keys in DESIGN_TABLE.items():
        for missing in sorted(keys):
            with pytest.raises(InvalidRequest):
                build(effect, outcome, keys - {missing})


@pytest.mark.parametrize(
    "effect,outcome",
    [
        ("read_source", "committed"),
        ("write_artifact", "ok"),
        ("record_disposition", "ok"),
        ("attach_source", "ok"),
        ("search_sources", "committed"),
    ],
)
def test_receipt_outcome_must_match_the_effect_row(effect, outcome):
    """an effect cannot report an outcome its row does not have"""
    with pytest.raises(InvalidRequest):
        Receipt(
            operation_id=OPERATION_ID, effect=effect, outcome=outcome, fields={"subject": "career"}
        )


def test_read_source_receipt_shapes_are_exact():
    """both read modes construct, and each refuses the other's field set"""
    configured = build("read_source", "ok", DESIGN_TABLE[("read_source", "ok")])
    captured = build("read_source", "ok", READ_SOURCE_CAPTURED)
    assert set(configured.fields) == DESIGN_TABLE[("read_source", "ok")]
    assert set(captured.fields) == READ_SOURCE_CAPTURED
    for keys in (DESIGN_TABLE[("read_source", "ok")], READ_SOURCE_CAPTURED):
        for missing in sorted(keys):
            if missing in ("root_ref", "ref"):
                continue
            with pytest.raises(InvalidRequest):
                build("read_source", "ok", keys - {missing})


def test_read_source_receipt_discriminant_is_exclusive():
    """a read receipt names exactly one of root_ref or ref"""
    with pytest.raises(InvalidRequest) as both:
        build("read_source", "ok", READ_SOURCE_CAPTURED | {"root_ref"})
    assert "exactly one" in both.value.message
    with pytest.raises(InvalidRequest) as neither:
        build("read_source", "ok", {"subject", "relative_path", "sha256", "bytes"})
    assert "exactly one" in neither.value.message


def test_open_work_receipt_modes_are_exact():
    """the mutating and non-mutating open modes are distinguishable shapes"""
    committed = build("open_work", "committed", DESIGN_TABLE[("open_work", "committed")])
    ephemeral = build("open_work", "ok", DESIGN_TABLE[("open_work", "ok")])
    assert "created_at" in committed.fields
    assert "created_at" not in ephemeral.fields
    with pytest.raises(InvalidRequest):
        build("open_work", "ok", DESIGN_TABLE[("open_work", "committed")])
    with pytest.raises(InvalidRequest):
        build("open_work", "committed", DESIGN_TABLE[("open_work", "ok")])


def test_write_success_requires_validated_receipt():
    """a success response refuses a missing or unvalidated receipt"""
    for effect, outcome in DESIGN_TABLE:
        with pytest.raises(InvalidRequest):
            success_response(effect, OPERATION_ID, {}, None)
    with pytest.raises(InvalidRequest):
        success_response("read_source", OPERATION_ID, {}, {"outcome": "ok"})


def test_response_refuses_a_foreign_receipt():
    """a receipt belongs to one operation of one effect"""
    receipt = build("open_work", "ok", DESIGN_TABLE[("open_work", "ok")])
    with pytest.raises(InvalidRequest):
        success_response("open_work", OTHER_ID, {}, receipt)
    with pytest.raises(InvalidRequest):
        success_response("search_sources", OPERATION_ID, {}, receipt)
    with pytest.raises(InvalidRequest):
        error_response(
            "search_sources", OPERATION_ID, WorkError("not_found", "no"), receipt
        )
    # a failure legitimately carries no receipt at all
    assert error_response(
        "search_sources", OPERATION_ID, WorkError("not_found", "no")
    )["receipt"] is None


def test_w0a_receipt_call_sites_unchanged():
    """make_receipt keeps its signature and still builds W0a's own receipts"""
    receipt = make_receipt(
        OPERATION_ID,
        "search_sources",
        "ok",
        subject="career",
        result_count=2,
    )
    assert receipt.as_dict()["result_count"] == 2


def test_w0a_envelope_fixture_updated_once():
    """exactly one committed W0a test is edited, and only its fixture"""
    from pathlib import Path

    source = Path(__file__).with_name("test_work_envelope.py").read_text("utf-8")
    # the corrected fixture, in the configured-root form
    assert '"read_source",\n        "ok",' in source
    assert '"read_source", "committed"' not in source.split("def test_success_response_shape")[1].split("def ")[0]
    # the refusal cases still build a receipt the allowlist refuses first
    refusals = source.split("def test_receipt_rejects_unknown_key")[1]
    assert 'content="the whole letter"' in refusals
    assert 'absolute_path="/private/x"' in refusals
    assert 'make_receipt("not-a-uuid"' in refusals
