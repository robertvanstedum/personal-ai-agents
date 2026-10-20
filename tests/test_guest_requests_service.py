"""Guest-request domain service: both stores, exact resolution, verified reject."""
from datetime import datetime, timezone

import pytest

from minimoi_portal import auth, guest_requests as gr


class FakeGuildDB:
    """In-memory stand-in for guild.guest_requests with the service's SQL shapes."""

    def __init__(self, rows):
        self.rows = {r["id"]: dict(r) for r in rows}
        self.fail = False

    def query(self, sql, params=()):
        if self.fail:
            raise RuntimeError("db down")
        if sql.startswith("SELECT count(*)"):
            open_rows = [r for r in self.rows.values() if r["status"] == "requested"]
            oldest = min((r["requested_at"] for r in open_rows), default=None)
            return [{"n": len(open_rows), "oldest": oldest}]
        if "WHERE id=%s" in sql:
            row = self.rows.get(params[0])
            return [dict(row)] if row else []
        if "WHERE status=%s" in sql:
            return sorted((dict(r) for r in self.rows.values() if r["status"] == params[0]),
                          key=lambda r: r["requested_at"])
        raise AssertionError(sql)

    def grant_if_requested(self, request_id):
        if getattr(self, "fail_grant", False):
            raise RuntimeError("connection lost")
        row = self.rows.get(request_id)
        changed = None
        if row and row["status"] == "requested":
            row["status"] = "granted"
            changed = {"id": request_id}
        return changed, (dict(row) if row else None)

    def reject_if_requested(self, request_id):
        row = self.rows.get(request_id)
        changed = None
        if row and row["status"] == "requested":
            row["status"] = "rejected"
            row["actioned_at"] = datetime(2026, 10, 20, 18, 0, tzinfo=timezone.utc)
            changed = {"id": request_id}
        return changed, (dict(row) if row else None)


def _row(i, name, status="requested", day=1):
    return {"id": i, "name": name, "email": f"{name.split()[0].lower()}{i}@x.test",
            "domain": "portuguese", "status": status,
            "requested_at": datetime(2026, 10, day, tzinfo=timezone.utc), "actioned_at": None}


@pytest.fixture
def auth_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "AUTH_DIR", tmp_path)
    return tmp_path


def test_lists_both_stores_with_qualified_refs(auth_dir):
    db = FakeGuildDB([_row(1, "Robert Test", day=2), _row(2, "Old", status="granted")])
    auth.create_pending("Reg User", "reg@x.test", "pw")
    found = gr.list_requests(db)
    assert [r.store for r in found] == ["guild", "pending"]
    assert found[0].ref == "guild:1"
    assert found[1].ref.startswith("pending:")


def test_resolve_is_exact_and_returns_all_matches(auth_dir):
    db = FakeGuildDB([_row(1, "Robert Test"), _row(2, "robert test", day=3), _row(3, "Robert")])
    both = gr.resolve("Robert Test", db)
    assert {r.ref for r in both.matches} == {"guild:1", "guild:2"}
    assert both.complete and both.unique is None
    one = gr.resolve("robert3@x.test", db)
    assert one.unique.ref == "guild:3"
    assert gr.resolve("Rob", db).matches == ()
    assert gr.resolve("  ", db).matches == ()


def test_reject_verifies_and_excludes_from_nudges(auth_dir):
    db = FakeGuildDB([_row(7, "Robert Test")])
    out = gr.reject("guild:7", db)
    assert out.status == "done"
    assert out.verified_state["status"] == "rejected"
    assert out.verified_state["nudge_eligible"] is False
    assert gr.pending_summary(db)["stores"]["guild"]["count"] == 0


def test_repeat_reject_is_already_applied(auth_dir):
    db = FakeGuildDB([_row(7, "Robert Test")])
    gr.reject("guild:7", db)
    assert gr.reject("guild:7", db).status == "already_applied"


def test_granted_request_is_never_flipped(auth_dir):
    db = FakeGuildDB([_row(8, "Granted Person", status="granted")])
    out = gr.reject("guild:8", db)
    assert out.status == "conflict"
    assert db.rows[8]["status"] == "granted"


def test_unknown_and_malformed_refs(auth_dir):
    db = FakeGuildDB([])
    assert gr.reject("guild:99", db).status == "not_found"
    assert gr.reject("guild:abc", db).status == "invalid_ref"
    assert gr.reject("other:1", db).status == "invalid_ref"
    assert gr.reject("pending:nope", db).status == "not_found"


def test_reject_file_registration(auth_dir):
    entry = auth.create_pending("Reg User", "reg@x.test", "pw")
    out = gr.reject(f"pending:{entry['token']}", FakeGuildDB([]))
    assert out.status == "done"
    assert auth.load_pending() == []


def test_summary_is_read_only_and_reports_partial_coverage(auth_dir):
    db = FakeGuildDB([_row(1, "A", day=4), _row(2, "B", day=2)])
    auth.create_pending("Reg", "reg@x.test", "pw")
    s = gr.pending_summary(db)
    assert s["coverage"] == "complete"
    assert s["stores"]["guild"] == {"count": 2, "oldest": "2026-10-02T00:00:00+00:00"}
    assert s["stores"]["pending"]["count"] == 1
    db.fail = True
    (auth_dir / "pending.json").write_text("{broken")
    s = gr.pending_summary(db)
    assert s["coverage"] == "partial"
    assert s["stores"]["guild"]["count"] is None
    assert s["stores"]["pending"]["error"] == "unreadable"


def test_get_by_ref(auth_dir):
    db = FakeGuildDB([_row(5, "Five")])
    assert gr.get("guild:5", db).name == "Five"
    assert gr.get("guild:6", db) is None
    entry = auth.create_pending("Reg", "reg@x.test", "pw")
    assert gr.get(f"pending:{entry['token']}", db).email == "reg@x.test"


# ── Codex review findings F1 and F2 (20 Oct) ─────────────────────────────────

def test_interrupted_approval_then_reject_is_conflict_not_success(auth_dir, monkeypatch):
    entry = auth.create_pending("Half Approved", "half@x.test", "pw")
    real_write = auth._write_json
    writes = []

    def crash_on_final_pending_write(filename, data):
        writes.append(filename)
        if filename == "pending.json" and writes.count("pending.json") == 2:
            raise OSError("crash after the guest was written")
        real_write(filename, data)

    monkeypatch.setattr(auth, "_write_json", crash_on_final_pending_write)
    with pytest.raises(OSError):
        auth.approve_pending(entry["token"])
    monkeypatch.setattr(auth, "_write_json", real_write)
    assert writes == ["pending.json", "guests.json", "pending.json"]
    assert auth.is_guest_active(entry["username"])

    out = gr.reject(f"pending:{entry['token']}", FakeGuildDB([]))
    assert out.status == "conflict"
    assert out.verified_state["status"] == "provisioned"
    # Rejecting did not silently revoke, and the registration is still there to finish.
    assert auth.is_guest_active(entry["username"])
    assert [p["token"] for p in auth.load_pending()] == [entry["token"]]


def test_approval_retry_keeps_the_same_expiry(auth_dir, monkeypatch):
    entry = auth.create_pending("Retry", "retry@x.test", "pw")
    real_write = auth._write_json
    calls = {"n": 0}

    def crash_once_after_guest(filename, data):
        calls["n"] += 1
        if filename == "pending.json" and calls["n"] == 3:
            raise OSError("crash")
        real_write(filename, data)

    monkeypatch.setattr(auth, "_write_json", crash_once_after_guest)
    with pytest.raises(OSError):
        auth.approve_pending(entry["token"])
    first_expiry = next(g for g in auth.load_guests() if g["username"] == entry["username"])["expires_at"]
    monkeypatch.setattr(auth, "_write_json", real_write)
    guest = auth.approve_pending(entry["token"])
    assert guest["expires_at"] == first_expiry
    assert [g["username"] for g in auth.load_guests()] == [entry["username"]]
    assert auth.load_pending() == []


def test_marker_without_guest_can_still_be_rejected(auth_dir, monkeypatch):
    entry = auth.create_pending("Early Crash", "early@x.test", "pw")
    real_write = auth._write_json

    def crash_on_guest_write(filename, data):
        if filename == "guests.json":
            raise OSError("crash before the guest was written")
        real_write(filename, data)

    monkeypatch.setattr(auth, "_write_json", crash_on_guest_write)
    with pytest.raises(OSError):
        auth.approve_pending(entry["token"])
    monkeypatch.setattr(auth, "_write_json", real_write)
    assert not auth.is_guest_active(entry["username"])
    assert gr.reject(f"pending:{entry['token']}", FakeGuildDB([])).status == "done"


def test_unreadable_pending_store_makes_resolution_incomplete(auth_dir):
    (auth_dir / "pending.json").write_text("{not json")
    db = FakeGuildDB([_row(1, "Test Person")])
    res = gr.resolve("Test Person", db)
    assert [r.ref for r in res.matches] == ["guild:1"]
    assert res.complete is False
    assert res.unavailable == ("pending",)
    assert res.unique is None
    with pytest.raises(gr.StoreUnavailable):
        gr.list_requests(db)
    assert len(gr.list_requests(db, allow_partial=True)) == 1


def test_unavailable_db_makes_resolution_incomplete(auth_dir):
    auth.create_pending("Test Person", "tp@x.test", "pw")
    db = FakeGuildDB([])
    db.fail = True
    res = gr.resolve("Test Person", db)
    assert len(res.matches) == 1 and not res.complete and res.unavailable == ("guild",)


def test_duplicate_name_across_stores_needs_clarification(auth_dir):
    auth.create_pending("Same Name", "reg@x.test", "pw")
    res = gr.resolve("same name", FakeGuildDB([_row(4, "Same Name")]))
    assert res.complete and len(res.matches) == 2 and res.unique is None


def test_get_and_reject_on_unreadable_file_are_unknown_not_absent(auth_dir):
    entry = auth.create_pending("Reg", "reg@x.test", "pw")
    (auth_dir / "pending.json").write_text("{broken")
    with pytest.raises(gr.StoreUnavailable):
        gr.get(f"pending:{entry['token']}", FakeGuildDB([]))
    assert gr.reject(f"pending:{entry['token']}", FakeGuildDB([])).status == "unknown"


def test_wrong_shaped_pending_file_is_unavailable(auth_dir):
    (auth_dir / "pending.json").write_text('{"pending": [{"no_token": 1}]}')
    with pytest.raises(gr.StoreUnavailable):
        gr.list_requests(FakeGuildDB([]))


def test_summary_without_database_config_is_partial(auth_dir, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    s = gr.pending_summary()
    assert s["coverage"] == "partial"
    assert s["stores"]["guild"]["error"] == "unavailable"
    assert s["stores"]["pending"]["count"] == 0


def test_db_write_exception_is_unknown(auth_dir):
    class Boom(FakeGuildDB):
        def reject_if_requested(self, request_id):
            raise RuntimeError("connection lost after send")
    assert gr.reject("guild:1", Boom([_row(1, "X")])).status == "unknown"


# ── grant state machine ─────────────────────────────────────────────────────

def _grant_row(i=10, name="New Guest", email="new@x.test", status="requested"):
    row = _row(i, name, status=status)
    row["email"] = email
    return row


def test_grant_creates_account_marks_granted_and_returns_secret_once(auth_dir):
    db = FakeGuildDB([_grant_row()])
    out = gr.grant("guild:10", duration_days=7, db=db, password_factory=lambda: "Temp-Pass-1")
    assert out.status == "done"
    assert out.verified_state["status"] == "granted" and out.verified_state["account_active"]
    assert out.verified_state["must_change_password"] is True          # persisted this time
    assert out.delivery_secret == "Temp-Pass-1" and "Temp-Pass-1" not in repr(out)
    user = out.verified_state["username"]
    ok, _ = auth.authenticate("new@x.test", "Temp-Pass-1")
    assert ok and ok["username"] == user
    assert "Temp-Pass-1" not in (auth_dir / gr.INTENTS_FILE).read_text()
    again = gr.grant("guild:10", db=db)
    assert again.status == "already_applied" and again.delivery_secret is None
    assert len(auth.load_guests()) == 1


def test_existing_guest_email_is_a_conflict_not_a_replacement(auth_dir):
    keep = auth.create_guest("Old", "2099-01-01T00:00:00+00:00", "pw", email="new@x.test")
    db = FakeGuildDB([_grant_row()])
    out = gr.grant("guild:10", db=db)
    assert out.status == "conflict" and out.verified_state["error"] == "guest_email_exists"
    assert [g["username"] for g in auth.load_guests()] == [keep["username"]]
    assert db.rows[10]["status"] == "requested"


def test_db_failure_after_account_is_unknown_and_resumes_without_second_account(auth_dir):
    db = FakeGuildDB([_grant_row()])
    db.fail_grant = True
    first = gr.grant("guild:10", db=db, password_factory=lambda: "P1-aaaaaa")
    assert first.status == "unknown" and first.verified_state["account"] == "created"
    assert db.rows[10]["status"] == "requested"
    db.fail_grant = False
    second = gr.grant("guild:10", db=db, password_factory=lambda: "P2-bbbbbb")
    assert second.status == "done" and len(auth.load_guests()) == 1
    # the resumed grant issues a fresh usable credential for the approval email
    assert auth.authenticate("new@x.test", "P2-bbbbbb")[0] is not None
    assert auth.check_must_change_password(second.verified_state["username"])


def test_crash_after_account_before_intent_update_adopts_the_account(auth_dir, monkeypatch):
    db = FakeGuildDB([_grant_row()])
    real = gr._save_intent
    calls = {"n": 0}

    def crash_second_save(ref, intent):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("crash after create_guest")
        real(ref, intent)

    monkeypatch.setattr(gr, "_save_intent", crash_second_save)
    assert gr.grant("guild:10", db=db).status == "unknown"
    monkeypatch.setattr(gr, "_save_intent", real)
    out = gr.grant("guild:10", db=db, password_factory=lambda: "P3-cccccc")
    assert out.status == "done" and len(auth.load_guests()) == 1
    assert auth.authenticate("new@x.test", "P3-cccccc")[0] is not None


def test_grant_never_flips_a_rejected_request_and_validates_input(auth_dir):
    db = FakeGuildDB([_grant_row(status="rejected")])
    assert gr.grant("guild:10", db=db).status == "conflict"
    assert auth.load_guests() == []
    assert gr.grant("guild:10", duration_days=90, db=db).status == "conflict"
    assert gr.grant("pending:abc", db=db).status == "invalid_ref"
    assert gr.grant("guild:404", db=FakeGuildDB([])).status == "not_found"


def test_unreadable_account_store_is_unknown(auth_dir):
    (auth_dir / "guests.json").write_text("{broken")
    out = gr.grant("guild:10", db=FakeGuildDB([_grant_row()]))
    assert out.status == "unknown" and out.verified_state["store"] == "auth"


# ── Codex review of CC-8 94199980 ───────────────────────────────────────────

def test_interrupted_grant_never_takes_over_an_account_it_did_not_create(auth_dir, monkeypatch):
    """Intent saved, crash before the account: another path then creates a guest with the same
    email. The resumed grant must conflict and leave that account and its password alone."""
    db = FakeGuildDB([_grant_row()])

    def crash_create(*a, **k):
        raise OSError("crash before the account was written")
    monkeypatch.setattr(auth, "create_guest", crash_create)
    assert gr.grant("guild:10", db=db).status == "unknown"
    monkeypatch.undo()
    monkeypatch.setattr(auth, "AUTH_DIR", auth_dir)
    other = auth.create_guest("Other path", "2099-01-01T00:00:00+00:00", "their-own-pw", email="new@x.test")
    out = gr.grant("guild:10", db=db, password_factory=lambda: "would-take-over")
    assert out.status == "conflict" and out.verified_state["error"] == "guest_email_exists"
    assert auth.authenticate("new@x.test", "their-own-pw")[0]["username"] == other["username"]
    assert db.rows[10]["status"] == "requested"


def test_created_account_carries_the_intent_id_and_is_adopted_only_by_it(auth_dir, monkeypatch):
    db = FakeGuildDB([_grant_row()])
    real = gr._save_intent
    calls = {"n": 0}

    def crash_second_save(ref, intent):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("crash after create_guest")
        real(ref, intent)
    monkeypatch.setattr(gr, "_save_intent", crash_second_save)
    gr.grant("guild:10", db=db)
    monkeypatch.setattr(gr, "_save_intent", real)
    intent_id = gr._intents()["guild:10"]["intent_id"]
    assert auth.load_guests()[0]["grant_intent_id"] == intent_id
    out = gr.grant("guild:10", db=db, password_factory=lambda: "P4-dddddd")
    assert out.status == "done" and out.verified_state["credential_generation"] == 1   # first was never returned


def test_uncertain_results_carry_no_password_and_no_notifications(auth_dir):
    db = FakeGuildDB([_grant_row()])
    db.fail_grant = True
    out = gr.grant("guild:10", db=db, password_factory=lambda: "never-sent")
    assert out.status == "unknown" and out.delivery_secret is None
    assert not out.verified_state.get("notifications_pending")


def test_done_requires_active_account_with_must_change(auth_dir, monkeypatch):
    db = FakeGuildDB([_grant_row()])
    monkeypatch.setattr(auth, "is_guest_active", lambda username: False)
    out = gr.grant("guild:10", db=db)
    assert out.status == "unknown" and out.verified_state["error"] == "account_state_unverified"
    assert out.delivery_secret is None and out.verified_state["notifications_pending"] == []


def test_crash_after_db_granted_resumes_to_done_with_a_fresh_password(auth_dir, monkeypatch):
    db = FakeGuildDB([_grant_row()])
    real = gr._save_intent

    def crash_on_granted(ref, intent):
        if intent["state"] == "granted":
            raise OSError("crash after the request was marked granted")
        real(ref, intent)
    monkeypatch.setattr(gr, "_save_intent", crash_on_granted)
    first = gr.grant("guild:10", db=db, password_factory=lambda: "lost-1")
    assert first.status == "unknown" and first.delivery_secret is None
    assert db.rows[10]["status"] == "granted"
    monkeypatch.setattr(gr, "_save_intent", real)
    out = gr.grant("guild:10", db=db, password_factory=lambda: "P5-eeeeee")
    assert out.status == "done" and out.delivery_secret == "P5-eeeeee"
    assert auth.authenticate("new@x.test", "P5-eeeeee")[0] is not None
    assert auth.authenticate("new@x.test", "lost-1")[0] is None


def test_credential_delivery_boundary(auth_dir):
    db = FakeGuildDB([_grant_row()])
    out = gr.grant("guild:10", db=db, password_factory=lambda: "first-pw")
    gen = out.verified_state["credential_generation"]
    assert out.verified_state["delivery"] == "pending"
    # The caller lost the password before preparing its email: explicit reissue.
    again = gr.reissue_credential("guild:10", password_factory=lambda: "second-pw")
    assert again.status == "done" and again.delivery_secret == "second-pw"
    assert again.verified_state["credential_generation"] == gen + 1
    assert auth.authenticate("new@x.test", "first-pw")[0] is None
    assert auth.check_must_change_password(again.verified_state["username"])
    stale = gr.confirm_delivery("guild:10", gen)
    assert stale.status == "conflict" and stale.verified_state["error"] == "credential_superseded"
    assert gr.confirm_delivery("guild:10", gen + 1).status == "done"
    assert gr.confirm_delivery("guild:10", gen + 1).status == "already_applied"
    assert gr.reissue_credential("guild:10").verified_state["error"] == "delivery_already_confirmed"
    replay = gr.grant("guild:10", db=db)
    assert replay.status == "already_applied" and replay.delivery_secret is None
    assert replay.verified_state["delivery"] == "confirmed"


def test_confirm_and_reissue_need_a_granted_intent(auth_dir):
    assert gr.confirm_delivery("guild:10", 1).verified_state["error"] == "not_granted"
    assert gr.reissue_credential("guild:10").verified_state["error"] == "not_granted"
