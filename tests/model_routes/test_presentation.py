"""Canonical answers, not route dispatches, establish notice presentation."""
from core.model_routes.presentation import prepare

NORMAL = {"route_id": "plan-openai-strong", "provider": "openai", "model": "gpt-6-astra"}
MOVED = {"route_id": "api-xai-strong", "provider": "minimoi-gateway", "model": "minimoi-xai-strong",
         "switched_from": "plan-openai-strong", "switch_signal": "quota"}
LINE = "[The plan is unavailable; this turn used the API.]"


def test_failed_save_does_not_acknowledge_but_lost_ack_after_save_does():
    saved = {}
    rows, show = prepare([], "a", MOVED, LINE, saved.get)
    assert show
    rows, show = prepare(rows, "b", MOVED, LINE, saved.get)
    assert show  # a had no canonical answer
    saved["b"] = "Answer first.\n\n" + LINE  # save reached disk; caller can crash here
    rows, show = prepare(rows, "c", MOVED, LINE, saved.get)
    assert not show
    saved["c"] = "Another answer without chrome."
    rows, show = prepare(rows, "d", MOVED, LINE, saved.get)
    assert not show  # canonical presentation reference survives a suppressed turn


def test_real_change_and_return_to_normal_reset_notice_but_timestamps_do_not():
    saved = {"a": LINE}
    rows, _ = prepare([], "a", MOVED, LINE, saved.get)
    rows, show = prepare(rows, "b", {**MOVED, "at": "later", "minutes": 99}, LINE, saved.get)
    assert not show
    rows, show = prepare(rows, "c", {**MOVED, "switch_signal": "unavailable"}, LINE, saved.get)
    assert show
    rows, show = prepare(rows, "d", NORMAL, "", saved.get)
    saved["d"] = "Plan recovered."
    rows, show = prepare(rows, "e", MOVED, LINE, saved.get)
    assert show


def test_only_exact_canonical_line_counts_and_fresh_conversation_has_no_receipt():
    saved = {"a": "I quoted " + LINE}
    rows, _ = prepare([], "a", MOVED, LINE, saved.get)
    assert prepare(rows, "b", MOVED, LINE, saved.get)[1]
    assert prepare([], "c", MOVED, LINE, saved.get)[1]
    saved["a"] = LINE
    assert not prepare(rows, "b", MOVED, LINE, saved.get)[1]


def test_mc_metadata_reloads_and_preserves_owner_boundary(tmp_path):
    import pytest
    from minimoi_portal.guild_ui.conversations import ConversationStore, ConversationNotFound
    from minimoi_portal.guild_ui.api import _route_notice
    from types import SimpleNamespace
    store = ConversationStore(str(tmp_path), base_floor="test")
    conv, _ = store.create("robert", key="route-notice-test")
    saved = {}
    floor = SimpleNamespace(get_note=saved.get)
    notice = _route_notice(store, conv, "robert", floor, "reply-a", NORMAL)
    assert notice(MOVED, LINE)
    reloaded = ConversationStore(str(tmp_path), base_floor="test")
    assert _route_notice(reloaded, conv, "robert", floor, "reply-b", NORMAL)(MOVED, LINE)
    saved["reply-b"] = {"who": "master_craftsman", "author_kind": "agent", "text": "Answer.\n" + LINE}
    assert not _route_notice(reloaded, conv, "robert", floor, "reply-c", NORMAL)(MOVED, LINE)
    with pytest.raises(ConversationNotFound):
        reloaded.prepare_route_notice(conv["id"], "other", "foreign", MOVED, LINE, lambda _: LINE)
    # Owner text or an unrelated author cannot manufacture a presentation receipt.
    saved["reply-b"]["who"] = "robert"
    assert _route_notice(reloaded, conv, "robert", floor, "reply-d", NORMAL)(MOVED, LINE)


def test_cos_canonical_history_reload_failed_turn_and_recovery(tmp_path):
    from domains.cos.history import History
    from domains.cos.model_route import notice_for_turn
    history = History(tmp_path, "dev")
    cid = history.ensure_default()["conversation_id"]
    def begin(n):
        tid = "t-" + f"{n:024x}"
        history.begin({"schema": "minimoi.cos.send/1", "conversation_id": cid,
                       "turn_id": tid, "text": "Question", "channel": "web"})
        return tid
    a = begin(1)
    assert notice_for_turn(history, cid, a, NORMAL)(MOVED, LINE)
    history.fail(cid, a, "runtime_unavailable")
    b = begin(2)
    assert notice_for_turn(history, cid, b, NORMAL)(MOVED, LINE)
    history.answer(cid, b, "Answer first.\n\n" + LINE)
    history = History(tmp_path, "dev")
    c = begin(3)
    assert not notice_for_turn(history, cid, c, NORMAL)(MOVED, LINE)
    history.answer(cid, c, "Answer without repeated route warning.")
    d = begin(4)
    assert not notice_for_turn(history, cid, d, NORMAL)(MOVED, LINE)


def test_unavailable_canonical_read_keeps_warning_and_never_claims_delivery(tmp_path):
    from minimoi_portal.guild_ui.conversations import ConversationStore
    from minimoi_portal.guild_ui.api import _route_notice
    from types import SimpleNamespace
    store = ConversationStore(str(tmp_path), base_floor="test")
    conv, _ = store.create("robert", key="failed-read-test")
    saved = {"a": {"who": "master_craftsman", "author_kind": "agent", "text": LINE}}
    first = _route_notice(store, conv, "robert", SimpleNamespace(get_note=saved.get), "a", NORMAL)
    assert first(MOVED, LINE)
    def unavailable(_):
        raise OSError("canonical store unavailable")
    callback = _route_notice(store, conv, "robert", SimpleNamespace(get_note=unavailable), "b", NORMAL)
    assert callback(MOVED, LINE)
    rows = store.get(conv["id"], "robert")["route_notice_candidates"]
    assert [row["reference"] for row in rows] == ["a"]
