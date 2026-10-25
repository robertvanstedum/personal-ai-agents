"""TurnLedger checks that need no database: argument and outcome validation, and that an
unconfigured or unreachable database refuses (LedgerUnavailable) instead of dispatching."""
import pytest

from minimoi_portal.guild_ui.mc.turn_ledger import Claim, LedgerUnavailable, TurnLedger, _clean_outcome


def test_unconfigured_database_refuses():
    with pytest.raises(LedgerUnavailable):
        TurnLedger(lambda: None).admit(principal="p", conversation_id="c", note_request_id="n", turn_request_id="t")


def test_unreachable_database_refuses():
    def down(url):
        raise OSError("connection refused")
    with pytest.raises(LedgerUnavailable):
        TurnLedger(lambda: "postgresql://x", connect=down).admit(principal="p", conversation_id="c",
                                                                  note_request_id="n", turn_request_id="t")


@pytest.mark.parametrize("kw", [dict(principal=""), dict(conversation_id=None), dict(note_request_id="x" * 201),
                                dict(kind="private"), dict(attachment_ids=[1]), dict(topic_refs=["t"])])
def test_bad_arguments_are_refused_before_any_connection(kw):
    def never(url):
        raise AssertionError("no connection for a bad request")
    args = dict(principal="p", conversation_id="c", note_request_id="n", turn_request_id="t")
    args.update(kw)
    with pytest.raises(ValueError):
        TurnLedger(lambda: "postgresql://x", connect=never).admit(**args)


def test_outcome_is_an_allowlist():
    assert _clean_outcome(None) == {}
    good = {"turn_id": "f375654ea2ae485dbcff5de50eb3b89a", "backend_turn_id": "1073f833-0089-41e8-8440-b9860c2d6b07",
            "backend_kind": "cos", "status": "answered", "failure_class": None, "reply_note_id": 7, "history_saved": True,
            "operation": {"type": "board_postit", "saved": True, "id": 4, "repeated": False, "reason": "needs_text"}}
    assert _clean_outcome(good) == good
    for bad in ({"reply": "hi"}, {"message": "short"}, {"secret": "x"}, {"backend_kind": "human"},
                {"operation": {"type": "board_postit", "text": "buy milk"}}, {"operation": "saved"},
                {"status": "Answered!"}, {"turn_id": "has space"}, {"reply_note_id": True}, {"saved": True}, "text"):
        with pytest.raises(ValueError):
            _clean_outcome(bad)


@pytest.mark.parametrize("kw", [dict(attachment_ids=["d-xyz"]), dict(attachment_ids=["d-0123456789abcdef"] * 2),
                                dict(attachment_ids=[f"d-{i:016x}" for i in range(11)]),
                                dict(topic_refs=[{"topic_id": "t-1", "item_id": "i-1"}]),
                                dict(topic_refs=[{"topic_id": "t-0123456789ab", "item_id": "i-0123456789ab", "text": "x"}]),
                                dict(topic_refs=[{"topic_id": "t-0123456789ab", "item_id": "i-0123456789ab", "rev": "abc"}]),
                                dict(topic_refs=[{"topic_id": "t-0123456789ab", "item_id": f"i-{i:012x}"} for i in range(6)])])
def test_files_and_topic_items_are_bounded_ids(kw):
    def never(url):
        raise AssertionError("no connection for a bad request")
    args = dict(principal="p", conversation_id="c", note_request_id="n", turn_request_id="t")
    args.update(kw)
    with pytest.raises(ValueError):
        TurnLedger(lambda: "postgresql://x", connect=never).admit(**args)


def test_finish_refuses_an_unknown_state():
    with pytest.raises(ValueError):
        TurnLedger(lambda: "postgresql://x").finish(Claim(1, "t", 1), "queued")


def test_continue_needs_an_entry_id():
    with pytest.raises(ValueError):
        TurnLedger(lambda: "postgresql://x").continue_after("p", "1")


@pytest.mark.parametrize("method", ["admit", "enqueue"])
def test_image_and_document_ids_reach_ledger_storage(method):
    """An uploaded image must not be rejected as an invalid document before storage."""
    calls = []
    def unavailable(url):
        calls.append(url)
        raise OSError("synthetic unavailable database")
    led = TurnLedger(lambda: "postgresql://synthetic", connect=unavailable)
    with pytest.raises(LedgerUnavailable):
        getattr(led, method)(principal="p", conversation_id="c", note_request_id="n", turn_request_id="t",
                            attachment_ids=["12345678-1234-1234-1234-123456789abc", "d-0123456789abcdef"])
    assert calls


@pytest.mark.parametrize("ids", [
    ["12345678-1234-1234-1234-123456789abc"] * 2,
    ["12345678-1234-1234-1234-123456789abc/../../secret"],
    ["12345678123412341234123456789abc"],
    [["nested"]], [{}],
])
def test_bad_image_ids_never_reach_storage(ids):
    def never(url):
        raise AssertionError("invalid ids must not reach storage")
    with pytest.raises(ValueError):
        TurnLedger(lambda: "postgresql://synthetic", connect=never).admit(
            principal="p", conversation_id="c", note_request_id="n", turn_request_id="t", attachment_ids=ids)
