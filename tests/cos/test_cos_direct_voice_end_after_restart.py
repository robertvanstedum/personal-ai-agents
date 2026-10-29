"""Review S-2 on Chief of Staff's host (A4_FIX_BRIEF.md item 4): End closes a session she holds for Robert after a restart.

A restart is a new app instance over the same folder: her real adapter and FileStore (cos_voice_rig.build again on the
same tmp_path), the core's real routes, nothing of the old process's ledger. End names her one conversation ("owner", as
her page does); the session's durable start marker must name this owner and that conversation. S3 runs as production
does: admitted by MINIMOI_COS_DIRECT_VOICE_PRODUCTION, then restarted without it. No provider, no Keychain, no
microphone, no network."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cos_voice_rig as rig  # noqa: E402

from core.realtime_voice import direct_host as core  # noqa: E402
from domains.cos import direct_voice, voice_store  # noqa: E402

BASE, OWNER = rig.BASE, rig.OWNER
PRODUCTION = {**rig.ENV, "MINIMOI_ROLE": "production"}


def end(r, sid, headers=OWNER, **body):
    return r.client.post(BASE + f"/session/{sid}/end", headers=headers, json=body)


def store_of(r):
    return voice_store.FileStore(r.voice, recall=r.recall)


def test_end_after_a_restart_closes_her_session_from_its_start_marker(monkeypatch, tmp_path):
    before = rig.build(monkeypatch, tmp_path)
    sid = rig.start(before)
    rig.post(before, sid, [rig.event(before, "user", "t0", "The kiln needs a seal.")])
    after = rig.build(monkeypatch, tmp_path)                                      # the restart: same folder, new process
    answer = end(after, sid, conversation_id="owner", last_item_id="t0", unacknowledged=[])
    assert answer.status_code == 200 and answer.get_json() == {"ok": True, "ended": True, "capture": "incomplete"}
    marker = store_of(after).read_marker(voice_store.HANDLE, sid, "end")
    assert marker["capture"] == "incomplete" and marker["reason"] == "end" and "missing" not in marker
    again = end(after, sid, conversation_id="owner")
    assert again.status_code == 200 and again.get_json()["capture"] == "incomplete"           # a second End: harmless
    assert rig.ask(after, sid, "host-1", "t1").status_code == 403 and not after.turns          # still no consult on it


def test_end_after_a_restart_refuses_a_forged_id_another_owners_session_and_no_conversation(monkeypatch, tmp_path):
    before = rig.build(monkeypatch, tmp_path)
    mine = rig.start(before)
    theirs = rig.start(before, headers={**OWNER, "X-Minimoi-Auth-Id": "8"})               # another owner's session
    after = rig.build(monkeypatch, tmp_path)
    assert end(after, str(uuid.uuid4()), conversation_id="owner").status_code == 403       # forged
    assert end(after, theirs, conversation_id="owner").status_code == 403                  # not his
    assert end(after, mine).status_code == 403                                             # no conversation named
    assert end(after, mine, conversation_id="guild-1").status_code == 403                  # not hers
    store = store_of(after)
    assert store.read_marker(voice_store.HANDLE, theirs, "end") is None
    assert store.read_marker(voice_store.HANDLE, mine, "end") is None
    assert end(after, mine, conversation_id="owner").get_json()["ended"] is True


def test_s3_on_her_host_end_closes_an_open_session_after_admission_is_turned_off(monkeypatch, tmp_path):
    """Spec 165 §11.1 S2 -> S3 as production runs it: admitted by her production switch, a session starts; the process
    restarts without the switch (S3). End on the open session closes it; a fresh start is refused with NOT_ON."""
    s2 = rig.build(monkeypatch, tmp_path, environ={**PRODUCTION, direct_voice.PRODUCTION_SWITCH: "1"})
    sid = rig.start(s2)
    rig.post(s2, sid, [rig.event(s2, "user", "t0", "What is on my agenda?")])
    s3 = rig.build(monkeypatch, tmp_path, environ=dict(PRODUCTION))
    assert direct_voice.may_start(s3.env) is False
    closed = end(s3, sid, conversation_id="owner", last_item_id="t0", unacknowledged=[])
    assert closed.status_code == 200 and closed.get_json() == {"ok": True, "ended": True, "capture": "incomplete"}
    assert store_of(s3).read_marker(voice_store.HANDLE, sid, "end")["capture"] == "incomplete"
    refused = s3.client.post(BASE + "/bootstrap", headers=OWNER, json={"persona": "chief_of_staff", "provider": "openai",
                                                                        "conversation_id": "owner"})
    assert refused.status_code == 503 and refused.get_json()["error"] == core.NOT_ON and len(s3.mints) == 0
