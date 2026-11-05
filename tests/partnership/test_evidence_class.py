"""Addendum 1 AC01: which operations need the full Q01 chain and which keep lightweight D03 history, with the
representative paid and unpaid lookup cases."""
import pytest

from services.partnership_tasks.evidence_class import (CASES, FULL, LIGHT, Operation, check_lightweight_record,
                                                       classify)


@pytest.mark.parametrize("label", sorted(CASES))
def test_representative_cases(label):
    op, evidence, paid = CASES[label]
    got = classify(op)
    assert (got.evidence, got.needs_spending_attribution) == (evidence, paid), label
    assert bool(got.reasons) == (evidence == FULL)


def test_both_paid_and_unpaid_lookups_are_represented():
    lights = [CASES[k] for k in CASES if CASES[k][1] == LIGHT]
    assert any(paid for _, _, paid in lights) and any(not paid for _, _, paid in lights)


def test_the_label_does_not_decide_scope():
    assert classify(Operation("read_only_lookup", external_effect=True)).evidence == FULL
    assert classify(Operation("write_tool")).evidence == LIGHT


def test_lightweight_never_drops_d03_history_or_spending_attribution():
    paid = Operation("web_search", spend="metered_within_request")
    assert check_lightweight_record({"source": "s", "coverage": {}, "result": "r"}, paid) == \
        ["request_id", "spending_authority"]
    assert check_lightweight_record({"source": "s", "coverage": {}, "result": "r", "request_id": "q",
                                     "spending_authority": "allowance:req"}, paid) == []
    assert check_lightweight_record({"coverage": {}}, Operation("get")) == ["source", "result"]


def test_unknown_spend_kind_refuses():
    with pytest.raises(ValueError):
        classify(Operation("x", spend="free"))
