"""B2: publish_update_v1's own logic offline (CoS 1.2 spec section 4.4): the turn binding and the receipt store are
stubbed, so this holds the write, the read-back and the replay. test_publish_update_postgres.py covers the same tool on
real PostgreSQL and the real policy."""
import json
import types
from contextlib import contextmanager

import pytest

from core.activity.writer import ActivityWriter, today
from core.mc_tools import effects
from core.mc_tools.service import ToolService
from domains.cos.store import contracts

UPDATE = {"decisions_needed": [{"one_line": "Merge #462?"}], "next": ["B2"],
          "blocked": [{"one_line": "Dev switch-on", "who_can_unblock": "robert"}]}


class Effect:
    def __init__(self, log):
        self.log = log

    def finish(self, conn, kind, outcome, *, verified):
        self.log.append((kind, outcome["state"], verified))
        return {"receipt_id": "r-1"}

    def unknown(self, conn):
        self.log.append(("unknown",))


@pytest.fixture
def service(tmp_path, monkeypatch):
    svc = ToolService(lambda: None, principal="robert", environment="dev")
    svc.activity_update = ActivityWriter(tmp_path / "guild-build", domain="guild-build", part="update",
                                         metric="build_update", environment="dev", service="minimoi-portal")
    binding = types.SimpleNamespace(conversation_id="c1", note_request_id="n1", context=lambda: None)

    @contextmanager
    def tx():
        yield None
    monkeypatch.setattr(svc, "_tx", tx)
    monkeypatch.setattr(svc, "_bind", lambda conn, caller: binding)
    monkeypatch.setattr(svc, "_bind_again", lambda conn, caller, b: b)
    svc.log, svc.receipted = [], set()

    def exists(conn, principal, environment, op):
        return op in svc.receipted

    def begin(conn, b, req, *, operation_id, worker):
        svc.receipted.add(operation_id)
        return Effect(svc.log)
    monkeypatch.setattr(effects, "receipt_exists", exists)
    monkeypatch.setattr(effects, "begin_standing_effect", begin)
    monkeypatch.setattr(effects, "reconcile_effect", lambda conn, **kw: None)
    monkeypatch.setattr(effects.RECEIPTS, "read", lambda *a: {"receipt_id": "r-1"})
    return svc


CALLER = types.SimpleNamespace(rule=types.SimpleNamespace(tier=2))


def test_writes_mc_s_seat_reads_back_and_receipts(service):
    r = service._publish_update(UPDATE, CALLER, None)
    assert (r["status"], r["code"]) == ("done", "published")
    record = json.loads((service.activity_update.path(today())).read_text())
    assert contracts.errors("activity-record", None, record) == []
    assert record["detail"] == {"seat": "mc", **UPDATE} and record["part"] == "update"
    assert service.log == [("guild_build_publish_update", "published", True)]


def test_a_retry_of_the_same_update_writes_nothing_new(service):
    service._publish_update(UPDATE, CALLER, None)
    service._publish_update(UPDATE, CALLER, None)
    assert json.loads(service.activity_update.path(today()).read_text())["seq"] == 1


def test_off_or_not_tier_two_is_refused(service):
    assert service._publish_update(UPDATE, types.SimpleNamespace(rule=None), None)["status"] == "refused"
    service.activity_update = None
    assert service._publish_update(UPDATE, CALLER, None)["status"] == "refused"
