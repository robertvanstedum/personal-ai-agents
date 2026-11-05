"""B4 v0.6 worker: what a pass sends (only approved admissions with time left, stale ones to close, fences first),
and the notice port (one notice per operation, a repeat is the lookup, words from the packet only)."""
from datetime import datetime, timedelta, timezone

from services.partnership_tasks import worker as W
from services.partnership_tasks.notice_port import PortalNotices, words_for
from services.partnership_tasks.outbox import HarnessRefused
from tests.partnership.fake_harness import FakeHarness
from tests.partnership.synth import Flow, approve_admission, cmd, ref, service
from tests.partnership.test_outbox import BD, D, outbox, request_admission

NOW = datetime(2026, 11, 5, 10, 0, tzinfo=timezone.utc)


def row(op, operation="admit_bundle", state="pending", stale=False, approval=None, created="2026-11-05T09:00:00+00:00"):
    return {"operation_id": op, "operation": operation, "state": state, "stale": stale, "approval": approval,
            "bundle_digest": BD, "created_at": created}


def approval(minutes_ago, spent=None, digest_=BD):
    return {"event_id": "SE-x", "card_digest": digest_, "spent_at": spent,
            "approved_at": (NOW - timedelta(minutes=minutes_ago)).isoformat()}


def test_a_pass_sends_only_approved_admissions_with_time_left_stale_ones_to_close_and_fences_first():
    rows = [row("OB-a"), row("OB-b", approval=approval(1)), row("OB-c", approval=approval(14.5)),
            row("OB-d", approval=approval(1, spent="2026-11-05T09:59:00+00:00")),
            row("OB-e", approval=approval(1, digest_="sha256:" + "9" * 64)), row("OB-f", stale=True),
            row("OB-g", state="unknown"), row("OB-fence", operation="set_fence", created="2026-11-05T09:30:00+00:00"),
            row("OB-n", operation="notify")]
    chosen = [r["operation_id"] for r in W.choose(rows, NOW)]
    assert chosen[0] == "OB-fence"
    assert set(chosen) == {"OB-fence", "OB-b", "OB-f", "OB-g", "OB-n"}
    # OB-a: no approval; OB-c: under 60 s left; OB-d: spent; OB-e: approval for another digest.


class InProcessDrainer:
    def __init__(self, svc):
        self.svc = svc

    def outbox_pending(self):
        from services.partnership_tasks.reconstruct import outbox_queue
        return outbox_queue(self.svc, D)


def test_one_pass_end_to_end_with_the_synthetic_harness(tmp_path):
    svc = service(tmp_path / "tasks", clock=lambda: NOW.isoformat())
    f = Flow(svc); tid = f.promoted_task(); h = FakeHarness(tmp_path / "harness")
    request_admission(svc, tid)
    report = W.one_pass(drainer=InProcessDrainer(svc), svc=svc, harness=h, notices=None, door=None, token=D, clock=lambda: NOW)
    assert report["drained"] == [] and h.admissions() == []                         # waits for Robert
    approve_admission(svc, "OB-syn-0001")
    report = W.one_pass(drainer=InProcessDrainer(svc), svc=svc, harness=h, notices=None, door=None, token=D, clock=lambda: NOW)
    assert report["drained"] == [("OB-syn-0001", "acknowledged")] and h.admissions() == ["OB-syn-0001"]


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class FakeDoor:
    def __init__(self):
        self.items, self.calls = {}, []

    def post(self, url, data=None, timeout=None, allow_redirects=None, headers=None):
        import json
        body = json.loads(data)
        self.calls.append((url, body, headers["Authorization"]))
        cur = self.items.get(body["key"])
        if cur is not None and cur != body["words"]:
            return FakeResponse(409, {"error": "key_conflict"})
        self.items[body["key"]] = body["words"]
        return FakeResponse(200, {"code": "W-%d" % (list(self.items).index(body["key"]) + 1), "state": "open"})


def test_notices_raise_once_per_operation_and_a_repeat_is_the_lookup():
    packet = {"kind": "return_packet", "task_id": "TK-1", "outcome": "A reviewed checklist.",
              "accepted_set": "sha256:" + "a" * 64, "artifacts": [{"id": "x"}], "accepted_by": "mc",
              "disagreements_delivered": [{"reviewer": "codex", "verdict": "disagree"}]}
    ob = {"operation": "notify", "packet": packet, "bundle_digest": "sha256:" + "b" * 64}
    door = FakeDoor()
    port = PortalNotices("http://portal:5001", "t" * 40, lambda op: ob, session=door)
    first = port.deliver("NT-1", ob["bundle_digest"], task_id="TK-1", kind="return_packet", attempt=1)["receipt"]
    assert first == {"store": "notices", "id": "NT-1", "edition": "W-1", "digest": ob["bundle_digest"]}
    assert port.lookup("NT-1", attempt=1) == {"state": "applied", "receipt": first}
    assert len(door.items) == 1 and door.calls[0][0].endswith("/internal/notices/v1/raise")
    words = words_for(packet)
    assert "TK-1" in words and "codex disagree" in words and 10 <= len(words) <= 1500
    door.items["NT-1"] = "something else"
    try:
        port.deliver("NT-1", ob["bundle_digest"], task_id="TK-1", kind="return_packet", attempt=2)
        raise AssertionError("expected a conflict")
    except HarnessRefused as exc:
        assert exc.code == "operation_id_reused"


def test_the_worker_builds_productions_version_2_store_with_its_grant_catalogue():
    """Increment-2 package review Must 1: a version-2 authority needs the grant store, or the worker dies at start."""
    import json
    from pathlib import Path
    from core.mc_delegation_dispatcher import production
    from core.mc_delegation_dispatcher.store import Authority
    root = Path(__file__).resolve().parents[2]
    auth = Authority.parse(json.loads((root / "tests/mc_delegation_dispatcher/authority.test_values.json").read_text()))
    store = production.store(lambda: None, principal="robert", environment="dev", authority=auth)
    assert store is not None
    source = (root / "services/partnership_tasks/worker.py").read_text()
    assert "production.store(" in source and "DispatcherStore(" not in source
