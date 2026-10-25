"""Voiced guest and lesson outcomes on the STREAMING endpoint with the OpenClaw backend, as dev runs since
22 Oct (MINIMOI_GUILD_MC=openclaw, MINIMOI_GUILD_MC_STREAM=on): each voiced turn is one buffered model call, never a
stream, and an ordinary message on the same endpoint still streams."""
import json
import uuid

import pytest

from floor_helpers import load_portal, write_headers  # noqa: F401
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401
from test_jobs_proposal import Runtime, _rig  # noqa: F401
from test_mc_streaming import _events, _stream, nd  # noqa: F401
from test_mc_turns import _keep
from test_mc_upload_reading import _new
from test_mc_guest_commands_route import FakeCommands
from core.mc_domain_actions import guest_commands as gc


@pytest.fixture
def rig(floored, tmp_path, monkeypatch):
    portal = _rig(floored, tmp_path, monkeypatch, jobs_on=False)
    FakeCommands.calls, FakeCommands.pending = [], False
    monkeypatch.setattr(gc, "GuestCommands", FakeCommands)
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    ext = portal.app.extensions["guild_ui_next"]
    monkeypatch.setitem(ext, "feature_environ", {**(ext.get("feature_environ") or {}),
        "MINIMOI_MC_GUEST_ACTIONS": "1", "MINIMOI_MC_GUEST_ACTIONS_ENV": "dev", "MINIMOI_MC_VOICED_OUTCOMES": "1",
        "MINIMOI_MC_LESSONS": "1", "MINIMOI_MC_LESSONS_ENV": "dev", "MINIMOI_MC_LESSONS_DIR": str(tmp_path / "lessons")})
    portal.extra["runtime"].nonstream_text = "Kept, in my own words."
    return portal


def send(portal, text, cid):
    client = portal.owner(); token = portal.csrf(client)
    note = _keep(client, token, text, conversation_id=cid)
    return _stream(client, token, note["request_id"], request_id="req-" + uuid.uuid4().hex, conversation_id=cid)


def test_voiced_lesson_and_guest_outcomes_work_on_the_streaming_endpoint_with_openclaw(rig):
    client = rig.owner(); token = rig.csrf(client); cid = _new(client, token)
    runtime = rig.extra["runtime"]
    posts_before = len(runtime.posts)
    r1 = send(rig, "Remember: lead with the answer, then the detail.", cid)
    assert r1.status_code == 200 and r1.is_json, r1.get_data(as_text=True)[:300]
    b1 = r1.get_json()
    assert b1["operation"]["type"] == "lesson" and b1["operation"]["state"] == "proposed"
    assert b1["backend_kind"] == "openclaw"                       # MC voiced it (buffered, not streamed)
    r2 = send(rig, "Confirm", cid)
    b2 = r2.get_json()
    assert b2["operation"] == {"type": "lesson", "state": "verified", "lesson": 1}, b2
    assert b2["backend_kind"] == "openclaw"
    assert len(runtime.posts) == posts_before + 2                 # exactly one buffered model call per voiced turn
    assert all(p["stream"] is False for p in runtime.posts[-2:])  # voiced turns never stream
    r3 = send(rig, "Reject the guest request from Bruno", cid)
    b3 = r3.get_json()
    assert FakeCommands.calls == ["reject"] and b3["operation"]["type"] == "guest_action"
    # An ordinary message on the same endpoint still streams.
    runtime.script = [nd({"t": "delta", "text": "Hello "}), nd({"t": "delta", "text": "Robert."}),
                      nd({"t": "finish", "reason": "stop"}), nd({"t": "usage", "prompt_tokens": 5, "completion_tokens": 3})]
    r4 = send(rig, "What changed on dev today?", cid)
    assert r4.status_code == 200 and not r4.is_json
    kinds = [e.get("t") for e in _events(r4)]
    assert "delta" in kinds and ("finish" in kinds or "end" in kinds or "done" in kinds), kinds
