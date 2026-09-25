from uuid import uuid4
import pytest
from store import Problem
from test_transcript_snapshot import source


@pytest.mark.parametrize("kind",["decision","task","task_update"])
def test_consequential_kind_requires_guard_in_store(source,kind):
    store,room=source
    before=store.room("robert",room)["events"]
    with pytest.raises(Problem,match="require expected_context") as error:
        store.append("robert",str(uuid4()),room,dict(kind=kind,body="Synthetic action"))
    assert error.value.status==409
    assert store.room("robert",room)["events"]==before


def test_strict_decision_rejects_stale_guard_and_replays_committed_receipt(source):
    store,room=source
    proposal=store.append("robert","proposal",room,dict(kind="proposal",body="Propose"))["result"]
    guard=store.room("robert",room)["contribution_guard"]
    store.append("robert","concurrent",room,dict(body="Changed context"))
    payload=dict(kind="decision",body="Approve synthetic proposal",reference=proposal["id"],expected_context=guard)
    with pytest.raises(Problem,match="Room changed"):
        store.append("robert","decision",room,payload)
    payload["expected_context"]=store.room("robert",room)["contribution_guard"]
    saved=store.append("robert","decision",room,payload)
    store.append("robert","later",room,dict(body="Even newer"))
    assert store.append("robert","decision",room,payload)==saved


def test_http_route_cannot_omit_strict_guard(source):
    from app import create_app
    store,room=source
    client=create_app(store.root,testing=True).test_client()
    response=client.post(f"/api/v1/rooms/{room}/events",json=dict(kind="task",body="Synthetic",target="robert"),
        headers={"Authorization":"Bearer "+store.owner_key,"Idempotency-Key":"unguarded","Host":"127.0.0.1:18880"})
    assert response.status_code==409


def test_seed_utility_supplies_guards_and_replays_safely(source):
    from manage import seed
    store,_=source
    result=seed(store)
    assert seed(store)==result
    events=store.room("robert",result["meeting"])["events"]
    assert {"decision","task","task_update"} <= {e["kind"] for e in events}
