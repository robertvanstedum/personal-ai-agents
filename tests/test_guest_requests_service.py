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
    assert {r.ref for r in gr.resolve("Robert Test", db)} == {"guild:1", "guild:2"}
    assert [r.ref for r in gr.resolve("robert3@x.test", db)] == ["guild:3"]
    assert gr.resolve("Rob", db) == []
    assert gr.resolve("  ", db) == []


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
