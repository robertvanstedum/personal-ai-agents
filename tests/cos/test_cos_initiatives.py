"""#680 through CoS's bound tool: CoS files a parked professional initiative with a packet from Robert's own turn, in
its conversation or an owner-private meeting, reads it back, and says plainly when nothing was filed. Real history,
binding store, listener, inbox and task service over HTTP; synthetic tokens."""
import hashlib
from dataclasses import replace

import pytest

from domains.cos.binding import BindingStore, TurnBinder
from domains.cos.files import CosFiles
from domains.cos.history import History
from domains.cos.store.writer import StoreWriter
from domains.cos.task_tools import CosTaskTools
from domains.cos.tool_listener import create_app as listener_app
from services.partnership_tasks.client import TaskClient
from services.partnership_tasks.http_api import create_app as tasks_app
from tests.cos.test_cos_tasks_e2e import RUN, TOKEN, USER, Session, call, owner_turn
from tests.partnership.synth import service


def _rig(tmp_path, *, gate=True, cos=("read", "file")):
    tasks = service(tmp_path / "tasks")
    grant = {"cos": frozenset(cos), "mc": frozenset({"read"})}
    tasks.bindings._by_hash = {k: replace(b, professional_initiatives=grant.get(b.seat, frozenset()))
                               for k, b in tasks.bindings._by_hash.items()}
    session = Session(tasks_app(tasks, initiatives_root=(tmp_path / "pi") if gate else None))
    history = History(tmp_path / "conv", "dev")
    history.ensure_default()
    bindings = BindingStore(tmp_path / "b" / "b.sqlite3", "dev")
    bindings.start_epoch("e1")
    bindings.register_instance("a" * 32)
    for d in ("inbox", "outbox"):
        (tmp_path / d).mkdir()
    files = CosFiles(tmp_path / "inbox", tmp_path / "outbox", "/minimoi/cos/inbox", "/minimoi/cos/outbox")
    tools = CosTaskTools(platform=TaskClient("http://tasks:8772", "tok-platform", session=session),
                         cos=TaskClient("http://tasks:8772", "tok-cos", session=session),
                         history=history, environment="synthetic", bindings=bindings, files=files)
    app = listener_app(bindings=bindings, writer=StoreWriter(tmp_path / "store", "dev"),
                       token_sha256=hashlib.sha256(TOKEN.encode()).hexdigest(), wait_s=0, task_tools=tools)
    return tasks, history, bindings, files, app.test_client(), session


@pytest.fixture
def rig(tmp_path):
    return _rig(tmp_path)


def test_cos_files_a_parked_initiative_with_a_packet_and_reads_it_back(rig):
    tasks, history, bindings, files, client, _ = rig
    up = files.save_upload("brief.md", b"# Speaking series\nThree talks, parked until spring.\n")
    owner_turn(history, bindings, "File the speaking series as a parked initiative with the brief.")
    out = call(client, {"operation": "initiative_file", "title": "Speaking series",
                        "summary": "Three talks; parked until spring.", "packet_file_ids": [up["file_id"]]})
    assert out["status"] == "done", out
    pid = out["data"]["initiative_id"]
    assert pid.startswith("PI-") and out["data"]["state"] == "parked"
    assert out["message"].startswith(f"Filed as {pid}, parked: no work starts.")
    read = call(client, {"operation": "initiative_read", "initiative_id": pid}, call_id="c2")
    packet = read["data"]["initiative"]["packet"]
    assert [p["name"] for p in packet] == ["brief.md"] and packet[0]["sha256"] == up["sha256"]
    listed = call(client, {"operation": "initiative_list"}, call_id="c3")
    assert [i["id"] for i in listed["data"]["initiatives"]] == [pid]
    # MC reads the same record with its own seat.
    mc = TaskClient("http://tasks:8772", "tok-mc", session=rig[5])
    assert mc.initiatives("read", {"initiative_id": pid}, scope="personal")["initiative"]["state"] == "parked"


def test_cos_attaches_a_file_to_an_existing_initiative(rig):
    tasks, history, bindings, files, client, _ = rig
    owner_turn(history, bindings, "File the board seat idea.")
    pid = call(client, {"operation": "initiative_file", "title": "Board seat", "summary": "Later."})["data"]["initiative_id"]
    up = files.save_upload("notes.txt", b"Shortlist of boards.")
    owner_turn(history, bindings, "Add my notes to it.", n=2)
    out = call(client, {"operation": "initiative_attach", "initiative_id": pid, "file_id": up["file_id"]}, n=2)
    assert out["status"] == "done" and out["message"] == f"Added to {pid} (still parked).", out


def test_an_owner_private_meeting_turn_can_file(rig):
    tasks, history, bindings, files, client, _ = rig
    assert TurnBinder(bindings).begin(turn_id="m1", session_user=USER, agent_entry="cos-meeting", conversation_id="r",
                                      meeting_scope="owner_private",
                                      binding={"origin": "meeting", "principal": "robert", "audience": "room-1",
                                               "claim_id": "c1", "room_generation": "g1",
                                               "owner_words": "CoS, file this as a parked initiative."})
    assert bindings.attach_run("m1", RUN.format(7))
    h = {"Authorization": "Bearer " + TOKEN, "X-CoS-Session": "agent:cos-meeting:openai-user:" + USER,
         "X-CoS-Agent": "cos-meeting", "X-CoS-Run": RUN.format(7), "X-CoS-Tool-Call": "m-1", "X-CoS-Instance": "a" * 32}
    out = client.post("/internal/cos/v1/tools/cos_tasks", headers=h,
                      json={"arguments": {"operation": "initiative_file", "title": "From the meeting",
                                          "summary": "Parked."}}).json
    assert out["status"] == "done", out
    mc = TaskClient("http://tasks:8772", "tok-mc", session=rig[5])
    kept = mc.initiatives("read", {"initiative_id": out["data"]["initiative_id"]}, scope="personal")["initiative"]
    assert kept["source"]["store"] == "cos-meeting-owner" and kept["source"]["id"] == "guildhall:room-1:m1"
    words = tasks.get("source_event", kept["source"]["source_event_id"], "tok-principal")
    assert words["content"].startswith("CoS, file this as a parked initiative.")


def test_a_telegram_or_mc_relayed_turn_cannot_file(rig):
    tasks, history, bindings, files, client, _ = rig
    owner_turn(history, bindings, "File it.", origin="service:mc", with_history=False)
    out = call(client, {"operation": "initiative_file", "title": "x", "summary": "y"})
    assert out["status"] == "refused" and out["code"] == "private_owner_turn_required"
    assert out["message"].startswith("Nothing was filed")


def test_gate_off_says_nothing_was_filed(tmp_path):
    tasks, history, bindings, files, client, _ = _rig(tmp_path, gate=False)
    owner_turn(history, bindings, "File it.")
    out = call(client, {"operation": "initiative_file", "title": "x", "summary": "y"})
    assert out["status"] == "refused" and out["code"] == "professional_initiatives_disabled", out
    assert out["message"] == "Nothing was filed: the professional-initiative store is switched off on this server."


def test_work_save_refused_by_a_disabled_service_says_nothing_was_saved(tmp_path):
    # #680 "also in scope": production answered 503 work_artifacts_disabled and CoS said "not fully verified".
    tasks, history, bindings, files, client, _ = _rig(tmp_path)
    owner_turn(history, bindings, "Save the draft.")
    out = call(client, {"operation": "work_save", "title": "Draft", "content": "Text", "expected_revision": 0,
                        "unresolved": []})
    assert out["status"] == "refused" and out["code"] == "work_artifacts_disabled", out
    assert out["message"].startswith("Nothing was saved:")
