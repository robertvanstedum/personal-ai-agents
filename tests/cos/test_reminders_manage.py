"""Reminder list / cancel / edit (MC 1.2 domain operations) and their interaction with delivery."""
from datetime import datetime, timedelta, timezone

import pytest

from domains.cos import reminders as r

NOW = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)


def _add(root, minutes, text="call the dentist", op=None):
    return r.add(NOW + timedelta(minutes=minutes), text, op or f"op-{minutes}-{text}", now=NOW, root=root)["reminder"]


def test_list_orders_by_due_and_filters(tmp_path):
    late = _add(tmp_path, 120, "later")
    soon = _add(tmp_path, 30, "sooner")
    assert [x["id"] for x in r.list_reminders(root=tmp_path)] == [soon["id"], late["id"]]
    r.cancel(late["id"], now=NOW, root=tmp_path)
    assert [x["id"] for x in r.list_reminders(states={"pending"}, root=tmp_path)] == [soon["id"]]


def test_list_unreadable_store_raises_not_empty(tmp_path):
    (tmp_path / r.STORE_FILE).write_text("{broken")
    with pytest.raises(r.ReminderStoreError):
        r.list_reminders(root=tmp_path)
    assert r.list_reminders(root=tmp_path / "missing") == []


def test_cancel_pending_is_kept_and_never_sent(tmp_path):
    rec = _add(tmp_path, 5)
    out = r.cancel(rec["id"], now=NOW, root=tmp_path)
    assert out["outcome"] == "cancelled" and out["reminder"]["state"] == "cancelled"
    sent = []
    summary = r.deliver_due(lambda text: sent.append(text) or {"ok": True, "message_id": 1},
                            now=NOW + timedelta(hours=1), root=tmp_path)
    assert sent == [] and summary["due"] == 0
    assert r.cancel(rec["id"], now=NOW, root=tmp_path)["outcome"] == "already_cancelled"


def test_cancel_refuses_in_flight_and_reports_delivered(tmp_path):
    rec = _add(tmp_path, 1)
    seen = {}

    def send(text):
        # While the message is going out, a cancel must not claim to have stopped it.
        seen["during"] = r.cancel(rec["id"], now=NOW, root=tmp_path)["outcome"]
        return {"ok": True, "message_id": 42}

    r.deliver_due(send, now=NOW + timedelta(minutes=2), root=tmp_path)
    assert seen["during"] == "in_flight"
    assert r.cancel(rec["id"], now=NOW, root=tmp_path)["outcome"] == "already_delivered"


def test_cancel_failed_reminder_stops_retries(tmp_path):
    rec = _add(tmp_path, 1)

    def fail(text):
        raise r.DeliveryFailed("telegram_rejected")

    r.deliver_due(fail, now=NOW + timedelta(minutes=2), root=tmp_path)
    assert r.cancel(rec["id"], now=NOW, root=tmp_path)["outcome"] == "cancelled"
    sent = []
    r.deliver_due(lambda t: sent.append(t) or {"ok": True, "message_id": 1},
                  now=NOW + timedelta(hours=1), root=tmp_path)
    assert sent == []


def test_cancel_uncertain_is_left_alone(tmp_path):
    rec = _add(tmp_path, 1)

    def maybe(text):
        raise r.DeliveryUncertain("timeout_after_send")

    r.deliver_due(maybe, now=NOW + timedelta(minutes=2), root=tmp_path)
    out = r.cancel(rec["id"], now=NOW, root=tmp_path)
    assert out["outcome"] == "uncertain" and out["reminder"]["state"] == "uncertain"


def test_cancel_not_found(tmp_path):
    assert r.cancel("rem-nope", now=NOW, root=tmp_path)["outcome"] == "not_found"
    _add(tmp_path, 5)
    assert r.cancel("rem-nope", now=NOW, root=tmp_path)["outcome"] == "not_found"
    with pytest.raises(r.ReminderRefused):
        r.cancel(" ", now=NOW, root=tmp_path)


def test_edit_changes_due_and_text_with_add_limits(tmp_path):
    rec = _add(tmp_path, 30)
    new_due = NOW + timedelta(hours=3)
    out = r.edit(rec["id"], due=new_due, text="  call   the vet ", expected_due_utc=rec["due_utc"],
                 now=NOW, root=tmp_path)
    assert out["text"] == "call the vet" and out["due_utc"] == r.format_utc(new_due)
    for kwargs, code in (({"due": NOW - timedelta(hours=1)}, "reminder_due_past"),
                         ({"due": NOW + timedelta(days=400)}, "reminder_due_too_far"),
                         ({"text": "   "}, "reminder_text_empty"),
                         ({}, "reminder_edit_empty"),
                         ({"text": "x", "expected_due_utc": rec["due_utc"]}, "reminder_changed")):
        with pytest.raises(r.ReminderRefused) as exc:
            r.edit(rec["id"], now=NOW, root=tmp_path, **kwargs)
        assert str(exc.value) == code or exc.value.args[0] == code


def test_edit_refuses_anything_not_pending(tmp_path):
    rec = _add(tmp_path, 1)
    r.deliver_due(lambda t: {"ok": True, "message_id": 7}, now=NOW + timedelta(minutes=2), root=tmp_path)
    with pytest.raises(r.ReminderRefused):
        r.edit(rec["id"], text="too late", now=NOW, root=tmp_path)
    other = _add(tmp_path, 60, "other")
    r.cancel(other["id"], now=NOW, root=tmp_path)
    with pytest.raises(r.ReminderRefused):
        r.edit(other["id"], text="revive", now=NOW, root=tmp_path)


def test_edited_reminder_is_sent_at_its_new_time(tmp_path):
    rec = _add(tmp_path, 5)
    r.edit(rec["id"], due=NOW + timedelta(hours=2), now=NOW, root=tmp_path)
    sent = []
    send = lambda t: sent.append(t) or {"ok": True, "message_id": 9}
    r.deliver_due(send, now=NOW + timedelta(minutes=10), root=tmp_path)
    assert sent == []
    r.deliver_due(send, now=NOW + timedelta(hours=2, minutes=1), root=tmp_path)
    assert sent == ["Reminder: call the dentist"]
