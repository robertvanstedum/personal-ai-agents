"""The COS reminder store: durable receipts, and never a duplicate send.

Every test uses a temporary store and a fake sender; nothing reaches Telegram.
"""

from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
import stat
from uuid import uuid4

import pytest

from domains.cos import reminders as R


NOW = datetime(2026, 10, 19, 13, 0, tzinfo=timezone.utc)
DUE = datetime(2026, 10, 19, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def root(tmp_path):
    return tmp_path / "cos_reminders"


def _add(root, text="call the plumber", due=DUE, operation_id=None, now=NOW):
    return R.add(due, text, operation_id or str(uuid4()), now=now, root=root)


def _document(root):
    return json.loads((root / R.STORE_FILE).read_text())


def _records(root):
    return _document(root)["reminders"]


class FakeSender:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.sent = []

    def __call__(self, text):
        self.sent.append(text)
        outcome = self.outcomes.pop(0) if self.outcomes else {"ok": True, "message_id": len(self.sent)}
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


# ── add ───────────────────────────────────────────────────────────────────────

def test_add_writes_one_durable_record_in_a_private_directory(root):
    operation_id = str(uuid4())

    outcome = R.add(DUE, "  call\n the   plumber ", operation_id, now=NOW, root=root)

    assert outcome["saved"] is True and outcome["deduplicated"] is False
    record = outcome["reminder"]
    assert record["id"].startswith("rem-") and len(record["id"]) == 16
    int(record["id"][4:], 16)
    assert record == {
        "id": record["id"],
        "operation_id": operation_id,
        "text": "call the plumber",
        "due_utc": "2026-10-19T14:00:00Z",
        "created_utc": "2026-10-19T13:00:00Z",
        "state": "pending",
        "attempts": 0,
        "last_attempt_utc": None,
        "delivered_utc": None,
        "telegram_message_id": None,
        "error_code": None,
    }
    assert _document(root) == {"schema_version": 1, "reminders": [record]}
    assert stat.S_IMODE(os.stat(root).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(root / R.STORE_FILE).st_mode) == 0o600
    assert (root / R.LOCK_FILE).exists()
    # Atomic replace leaves no temporary files behind.
    assert sorted(p.name for p in root.iterdir()) == sorted([R.LOCK_FILE, R.STORE_FILE])


def test_store_directory_comes_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("COS_REMINDERS_DIR", str(tmp_path / "configured"))
    R.add(DUE, "env store", str(uuid4()), now=NOW)

    assert R.store_dir() == tmp_path / "configured"
    assert _records(tmp_path / "configured")[0]["text"] == "env store"


def test_same_operation_and_content_is_deduplicated(root):
    operation_id = str(uuid4())
    first = _add(root, operation_id=operation_id)

    retry = _add(root, text="call   the plumber", operation_id=operation_id)

    assert retry["deduplicated"] is True and retry["saved"] is True
    assert retry["reminder"]["id"] == first["reminder"]["id"]
    assert len(_records(root)) == 1


def test_retry_after_the_due_time_still_returns_the_stored_reminder(root):
    operation_id = str(uuid4())
    first = _add(root, operation_id=operation_id)

    retry = _add(root, operation_id=operation_id, now=DUE + timedelta(hours=2))

    assert retry["deduplicated"] is True
    assert retry["reminder"]["id"] == first["reminder"]["id"]


@pytest.mark.parametrize(
    "changes",
    [{"text": "call the electrician"}, {"due": DUE + timedelta(minutes=5)}],
    ids=["different-text", "different-due"],
)
def test_same_operation_with_different_content_is_refused(root, changes):
    operation_id = str(uuid4())
    _add(root, operation_id=operation_id)

    with pytest.raises(R.ReminderRefused) as refused:
        _add(root, operation_id=operation_id, **changes)

    assert refused.value.code == "reminder_idempotency_conflict"
    assert len(_records(root)) == 1


@pytest.mark.parametrize(
    ("due", "text", "code"),
    [
        (NOW - timedelta(seconds=121), "late", "reminder_due_past"),
        (NOW + timedelta(days=366, seconds=1), "far", "reminder_due_too_far"),
        (DUE, "   \n ", "reminder_text_empty"),
        (DUE, "x" * (R.MAX_TEXT_CHARS + 1), "reminder_text_too_long"),
        (datetime(2026, 10, 19, 14, 0), "naive", "reminder_due_naive"),
    ],
    ids=["past", "too-far", "empty", "too-long", "naive"],
)
def test_out_of_bounds_reminders_are_refused_and_not_stored(root, due, text, code):
    with pytest.raises(R.ReminderRefused) as refused:
        R.add(due, text, str(uuid4()), now=NOW, root=root)

    assert refused.value.code == code
    assert not (root / R.STORE_FILE).exists()


def test_bounds_allow_a_short_grace_period_and_a_full_year(root):
    _add(root, due=NOW - timedelta(seconds=120))
    _add(root, due=NOW + timedelta(days=366))
    _add(root, text="x" * R.MAX_TEXT_CHARS)

    assert len(_records(root)) == 3


def test_open_reminder_limit_is_enforced(root):
    root.mkdir(mode=0o700)
    pending = [
        {"id": f"rem-{i:012x}", "operation_id": f"op-{i}", "text": "t",
         "due_utc": "2026-10-20T00:00:00Z", "state": "pending", "attempts": 0}
        for i in range(R.MAX_OPEN_REMINDERS)
    ]
    (root / R.STORE_FILE).write_text(json.dumps({"schema_version": 1, "reminders": pending}))

    with pytest.raises(R.ReminderRefused) as refused:
        _add(root)
    assert refused.value.code == "reminder_limit_reached"

    # Final records do not count against the limit.
    pending[0]["state"] = "delivered"
    (root / R.STORE_FILE).write_text(json.dumps({"schema_version": 1, "reminders": pending}))
    assert _add(root)["saved"] is True


def test_unreadable_store_is_never_overwritten(root):
    root.mkdir(mode=0o700)
    (root / R.STORE_FILE).write_text("{not json")

    with pytest.raises(R.ReminderStoreError):
        _add(root)
    with pytest.raises(R.ReminderStoreError):
        R.deliver_due(FakeSender(), now=DUE, root=root)
    assert (root / R.STORE_FILE).read_text() == "{not json"


# ── deliver_due ───────────────────────────────────────────────────────────────

def test_due_reminder_is_delivered_with_the_telegram_message_id(root):
    reminder_id = _add(root)["reminder"]["id"]
    send = FakeSender({"ok": True, "message_id": 777})

    summary = R.deliver_due(send, now=DUE + timedelta(minutes=1), root=root)

    assert send.sent == ["Reminder: call the plumber"]
    assert summary["due"] == 1 and summary["delivered"] == 1
    [record] = _records(root)
    assert record["id"] == reminder_id
    assert record["state"] == "delivered"
    assert record["telegram_message_id"] == 777
    assert record["delivered_utc"] == "2026-10-19T14:01:00Z"
    assert record["attempts"] == 1
    assert record["last_attempt_utc"] == "2026-10-19T14:01:00Z"
    assert record["error_code"] is None


def test_not_yet_due_reminder_is_untouched(root):
    _add(root)
    before = (root / R.STORE_FILE).read_text()
    send = FakeSender()

    summary = R.deliver_due(send, now=DUE - timedelta(seconds=1), root=root)

    assert send.sent == [] and summary["due"] == 0
    assert (root / R.STORE_FILE).read_text() == before
    assert R.has_work(now=DUE - timedelta(seconds=1), root=root) is False
    assert R.has_work(now=DUE, root=root) is True


def test_missing_store_is_idle_and_creates_nothing(root):
    assert R.has_work(now=DUE, root=root) is False
    assert R.deliver_due(FakeSender(), now=DUE, root=root)["due"] == 0
    assert not root.exists()


def test_late_delivery_says_when_it_was_due_in_chicago_time(root):
    _add(root)
    send = FakeSender()

    R.deliver_due(send, now=DUE + timedelta(minutes=11), root=root)

    assert send.sent == ["Reminder: call the plumber (was due Mon 19 Oct 09:00)"]


def test_delivery_within_ten_minutes_has_no_late_notice(root):
    _add(root)
    send = FakeSender()

    R.deliver_due(send, now=DUE + timedelta(minutes=10), root=root)

    assert send.sent == ["Reminder: call the plumber"]


def test_definite_failure_is_retried_after_five_minutes_at_most_three_times(root):
    _add(root)
    send = FakeSender(*(R.DeliveryFailed("telegram_http_502") for _ in range(4)))
    start = DUE + timedelta(minutes=1)

    first = R.deliver_due(send, now=start, root=root)
    assert first["failed"] == 1
    [record] = _records(root)
    assert record["state"] == "failed" and record["attempts"] == 1
    assert record["error_code"] == "telegram_http_502"

    # Not again within the retry interval.
    R.deliver_due(send, now=start + timedelta(minutes=4, seconds=59), root=root)
    assert len(send.sent) == 1

    R.deliver_due(send, now=start + timedelta(minutes=5), root=root)
    R.deliver_due(send, now=start + timedelta(minutes=10), root=root)
    assert len(send.sent) == 3
    assert _records(root)[0]["attempts"] == 3

    # A third failure is final for delivery purposes.
    R.deliver_due(send, now=start + timedelta(hours=1), root=root)
    assert len(send.sent) == 3
    assert _records(root)[0]["state"] == "failed"
    assert R.has_work(now=start + timedelta(days=1), root=root) is False


def test_failure_then_success_records_delivery(root):
    _add(root)
    send = FakeSender(R.DeliveryFailed("telegram_unreachable"), {"ok": True, "message_id": 9})

    R.deliver_due(send, now=DUE, root=root)
    R.deliver_due(send, now=DUE + timedelta(minutes=5), root=root)

    [record] = _records(root)
    assert record["state"] == "delivered" and record["telegram_message_id"] == 9
    assert record["attempts"] == 2 and record["error_code"] is None


@pytest.mark.parametrize(
    "outcome",
    [
        R.DeliveryUncertain("telegram_timeout"),
        RuntimeError("unexpected sender bug"),
        {"ok": True},
        {"ok": True, "message_id": True},
        None,
    ],
    ids=["uncertain", "unknown-exception", "no-message-id", "bool-message-id", "no-result"],
)
def test_ambiguous_send_becomes_uncertain_and_is_never_resent(root, outcome):
    _add(root)
    send = FakeSender(outcome)

    summary = R.deliver_due(send, now=DUE, root=root)

    assert summary["uncertain"] == 1
    [record] = _records(root)
    assert record["state"] == "uncertain"
    assert record["telegram_message_id"] is None
    for later in (timedelta(minutes=5), timedelta(hours=1), timedelta(days=30)):
        R.deliver_due(send, now=DUE + later, root=root)
    assert len(send.sent) == 1


def test_interrupted_send_becomes_uncertain_without_another_send(root):
    _add(root)
    document = _document(root)
    document["reminders"][0].update(
        state="sending", attempts=1, last_attempt_utc="2026-10-19T14:00:00Z",
    )
    (root / R.STORE_FILE).write_text(json.dumps(document))
    send = FakeSender()

    # Still within the window a live send may take: left alone.
    fresh = R.deliver_due(send, now=DUE + timedelta(minutes=10), root=root)
    assert fresh["stale_sending"] == 0 and _records(root)[0]["state"] == "sending"

    stale = R.deliver_due(send, now=DUE + timedelta(minutes=10, seconds=1), root=root)

    assert send.sent == []
    assert stale["stale_sending"] == 1
    [record] = _records(root)
    assert record["state"] == "uncertain" and record["error_code"] == "send_interrupted"


def test_sequential_runs_never_send_the_same_reminder_twice(root):
    for minute in range(3):
        _add(root, text=f"reminder {minute}", due=DUE + timedelta(minutes=minute))
    send = FakeSender()

    for minute in range(10):
        R.deliver_due(send, now=DUE + timedelta(minutes=minute), root=root)

    assert sorted(send.sent) == ["Reminder: reminder 0", "Reminder: reminder 1", "Reminder: reminder 2"]
    assert {record["state"] for record in _records(root)} == {"delivered"}


def test_send_runs_outside_the_store_lock(root):
    _add(root)
    lock_was_free = []

    def send(text):
        fd = os.open(root / R.LOCK_FILE, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)   # raises if held
            lock_was_free.append(True)
            # The claim is already durable before the send starts.
            assert _records(root)[0]["state"] == "sending"
        finally:
            os.close(fd)
        return {"ok": True, "message_id": 5}

    R.deliver_due(send, now=DUE, root=root)

    assert lock_was_free == [True]
    assert _records(root)[0]["state"] == "delivered"


def test_each_run_sends_a_bounded_batch_oldest_due_first(root):
    total = R.MAX_SENDS_PER_RUN + 3
    for index in range(total):
        _add(root, text=f"r{index:02d}", due=DUE + timedelta(seconds=total - index))
    send = FakeSender()
    later = DUE + timedelta(minutes=1)

    first = R.deliver_due(send, now=later, root=root)
    assert first["due"] == R.MAX_SENDS_PER_RUN and first["deferred"] == 3
    assert send.sent[0] == f"Reminder: r{total - 1:02d}"

    second = R.deliver_due(send, now=later, root=root)
    assert second["due"] == 3
    assert len(send.sent) == total == len(set(send.sent))


def test_one_malformed_record_does_not_stop_the_others(root):
    _add(root, text="good one")
    document = _document(root)
    document["reminders"].insert(0, {"id": "rem-000000000000", "state": "pending"})
    (root / R.STORE_FILE).write_text(json.dumps(document))
    send = FakeSender()

    summary = R.deliver_due(send, now=DUE, root=root)

    assert summary["invalid"] == 1 and summary["delivered"] == 1
    assert send.sent == ["Reminder: good one"]
    assert _records(root)[0] == {"id": "rem-000000000000", "state": "pending"}


def test_replay_cannot_confirm_until_directory_sync_recovers(root, monkeypatch):
    operation = str(uuid4())
    real = R.os.fsync
    def fail_directory(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError('directory sync unavailable')
        real(fd)
    monkeypatch.setattr(R.os, 'fsync', fail_directory)
    with pytest.raises(OSError):
        _add(root, operation_id=operation)
    assert len(_records(root)) == 1
    with pytest.raises(OSError):
        _add(root, operation_id=operation)
    monkeypatch.setattr(R.os, 'fsync', real)
    assert _add(root, operation_id=operation)['deduplicated'] is True
    assert len(_records(root)) == 1
