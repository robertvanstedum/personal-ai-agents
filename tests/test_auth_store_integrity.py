"""Account-file integrity: one lock, no silent wipe, explicit email-collision policy.

Covers the guest-writer inventory findings (Claude Code lane, 20 Oct 2026):
concurrent read-modify-write lost updates, a malformed file treated as empty and
overwritten, silent replacement of a guest on email collision, approve_pending
write order, and writes on the public verify GET.
"""
import json
import threading

import pytest

from minimoi_portal import auth


@pytest.fixture
def auth_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "AUTH_DIR", tmp_path)
    return tmp_path


def _guests(auth_dir):
    return json.loads((auth_dir / "guests.json").read_text())["guests"]


def test_concurrent_creates_lose_nothing(auth_dir):
    errors = []

    def make(i):
        try:
            auth.create_guest(f"G{i}", "2099-01-01T00:00:00+00:00", "pw", email=f"g{i}@x.test")
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=make, args=(i,)) for i in range(25)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert sorted(g["email"] for g in _guests(auth_dir)) == sorted(f"g{i}@x.test" for i in range(25))


def test_malformed_file_is_never_overwritten(auth_dir):
    damaged = '{"guests": [{"username": "guest_keep"'
    (auth_dir / "guests.json").write_text(damaged)
    with pytest.raises(auth.AuthStoreError):
        auth.create_guest("New", "2099-01-01T00:00:00+00:00", "pw", email="n@x.test")
    with pytest.raises(auth.AuthStoreError):
        auth.revoke_guest("guest_keep")
    with pytest.raises(auth.AuthStoreError):
        auth.extend_guest("guest_keep", 3)
    assert (auth_dir / "guests.json").read_text() == damaged
    # Readers still degrade instead of crashing the login path.
    assert auth.load_guests() == []


def test_non_object_json_is_refused(auth_dir):
    (auth_dir / "users.json").write_text("[]")
    with pytest.raises(auth.AuthStoreError):
        auth.reset_user_password("owner", "new")
    assert (auth_dir / "users.json").read_text() == "[]"


def test_missing_file_still_starts_empty(auth_dir):
    guest = auth.create_guest("First", "2099-01-01T00:00:00+00:00", "pw")
    assert [g["username"] for g in _guests(auth_dir)] == [guest["username"]]


def test_email_conflict_refuse_keeps_existing_guest(auth_dir):
    first = auth.create_guest("A", "2099-01-01T00:00:00+00:00", "pw1", email="Same@x.test")
    with pytest.raises(auth.GuestEmailConflict):
        auth.create_guest("B", "2099-01-01T00:00:00+00:00", "pw2", email="same@x.test",
                          on_email_conflict="refuse")
    assert [g["username"] for g in _guests(auth_dir)] == [first["username"]]


def test_email_conflict_default_replace_is_unchanged(auth_dir):
    auth.create_guest("A", "2099-01-01T00:00:00+00:00", "pw1", email="same@x.test")
    second = auth.create_guest("B", "2099-01-01T00:00:00+00:00", "pw2", email="same@x.test")
    assert [g["username"] for g in _guests(auth_dir)] == [second["username"]]


def test_unknown_conflict_policy_rejected(auth_dir):
    with pytest.raises(ValueError):
        auth.create_guest("A", "2099-01-01T00:00:00+00:00", "pw", email="a@x.test",
                          on_email_conflict="merge")


def test_approve_pending_writes_guest_before_removing_pending(auth_dir, monkeypatch):
    entry = auth.create_pending("P", "p@x.test", "pw")
    real_write = auth._write_json
    calls = []

    def crash_on_pending(filename, data):
        calls.append(filename)
        if filename == "pending.json":
            raise OSError("simulated crash between writes")
        real_write(filename, data)

    monkeypatch.setattr(auth, "_write_json", crash_on_pending)
    with pytest.raises(OSError):
        auth.approve_pending(entry["token"])
    assert calls == ["guests.json", "pending.json"]
    monkeypatch.setattr(auth, "_write_json", real_write)
    # The registration survives and a retry completes without duplicating the guest.
    assert [p["token"] for p in auth.load_pending()] == [entry["token"]]
    guest = auth.approve_pending(entry["token"])
    assert [g["username"] for g in _guests(auth_dir)] == [guest["username"]]
    assert auth.load_pending() == []


def test_consume_verification_unknown_token_does_not_write(auth_dir):
    token = auth.create_verification("v@x.test", "V", "why", "portuguese")
    path = auth_dir / "email_verifications.json"
    before = path.stat().st_mtime_ns
    assert auth.consume_verification("nope") is None
    assert path.stat().st_mtime_ns == before
    entry = auth.consume_verification(token)
    assert entry["email"] == "v@x.test"
    assert json.loads(path.read_text())["verifications"] == []


def test_reset_requests_unreadable_file_is_not_pruned_away(auth_dir):
    damaged = '{"requests": ['
    (auth_dir / "reset_requests.json").write_text(damaged)
    assert auth.load_reset_requests() == []
    assert (auth_dir / "reset_requests.json").read_text() == damaged


def test_lock_is_reentrant_across_nested_writers(auth_dir):
    # consume_reset_request holds the lock while calling load_reset_requests,
    # which takes it again; this must not deadlock.
    req = auth.create_reset_request("guest_x", "X", "x@x.test")
    assert auth.consume_reset_request(req["id"]) is True
    assert auth.load_reset_requests() == []
