"""Confirm by voice, v3, the fix round's integrated cases (REVIEW_v3_build.md S8): the real server's decisions and the
adapters' real result shapes, recorded for the real page. Each scenario runs through MC's real adapter on the synthetic
Guild mount and Chief of Staff's real adapter and file store (test_direct_contract's driver), with exactly the requests
the page makes in tests/realtime_voice/test_confirm_by_voice.mjs's S8 cases (the same owner item ids, the
question's after_item, the confirmation's fields); the answers are kept, normalized, in
tests/realtime_voice/data/confirm_by_voice_recorded.json, which that page rig replays through the real page, controller
and adapters on both providers. This test fails when the server's answers no longer match the recording (a server
change the page has not been run against): regenerate it with VOICE_RECORD=1 and run the page rig again.

The scenarios cover M1 (a note only the understanding call heard runs as his typed turn: the answer is the typed turn's
own, with no saved flag, so the page must say it, never "Saved.") and S3 (the first yes's answer is lost; the second
yes's "again" carries the action's own owner item and hash, and the page's one confirmation consumes it). Transcript
posts decide nothing, so they are not recorded. No provider, gateway, Keychain, microphone or network."""
import json
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "guild" / "shop_floor"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cos"))

from test_confirm_by_voice import NATURAL, REQUEST, asked, hears, utter  # noqa: E402
from test_direct_contract import driver, rig  # noqa: E402,F401  (the fixtures driver needs)

RECORDING = Path(__file__).resolve().parent / "data" / "confirm_by_voice_recorded.json"
PAGE_SID = "7c1d2e3f-4a5b-4c6d-8e9f-0a1b2c3d4e5f"          # tests/js_helpers/direct_voice_rig.mjs's SID, the page's
PAGE_CID = "c-1"                                          # the page rig's conversation


def scenario_m1(h, sid, step):
    """M1: a natural note the grammar does not hear; the call names it; after his yes it runs as his typed turn."""
    hears(h, {NATURAL: "note"})
    ask = step("utterance", {"owner_item_id": "t0", "text": NATURAL}, utter(h, sid, "t0", NATURAL))
    step("asked", {"action_id": ask["action_id"], "after_item": "t0"}, asked(h, sid, ask["action_id"], "t0"))
    yes = step("utterance", {"owner_item_id": "t2", "text": "Yes."}, utter(h, sid, "t2", "Yes."))
    confirm(h, sid, step, yes)


def scenario_s3(h, sid, step):
    """S3: an explicit note; the first yes's answer is lost on the way back (the page never sees it); a second yes."""
    ask = step("utterance", {"owner_item_id": "t0", "text": REQUEST[h.kind]}, utter(h, sid, "t0", REQUEST[h.kind]))
    step("asked", {"action_id": ask["action_id"], "after_item": "t0"}, asked(h, sid, ask["action_id"], "t0"))
    step("utterance", {"owner_item_id": "t2", "text": "Yes."}, utter(h, sid, "t2", "Yes."), lost=True)
    again = step("utterance", {"owner_item_id": "t4", "text": "Yes."}, utter(h, sid, "t4", "Yes."))
    confirm(h, sid, step, again)


def confirm(h, sid, step, yes):
    """The page's one confirmation, exactly as confirmed() in direct-voice-session.js builds it from the decision (a
    field the decision lacks is left out, as JSON.stringify leaves out undefined)."""
    body = {"session_id": sid, "call_id": "confirm-" + yes["action_id"], "action_id": yes["action_id"],
            **{k: yes[k] for k in ("confirm_item_id", "owner_item_id", "text_sha256") if k in yes}}
    step("consult", body, h.client.post(h.base + "/consult", headers=h.headers, json=body))


SCENARIOS = {"m1_natural_note_is_his_typed_turn": scenario_m1, "s3_second_yes_after_a_lost_answer": scenario_s3}


def normalized(value, sid):
    """The recording with its volatile values made stable: the session (dashed and not) as the page rig's, the
    conversation as the page rig's, and every other id (action ids, receipts) as a name in order of first appearance."""
    names = {sid: PAGE_SID, sid.replace("-", ""): PAGE_SID.replace("-", "")}
    pattern = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\b[0-9a-f]{32}\b")

    def text(s):
        def name(m):
            if m.group(0) not in names:
                kind = "action" if len(m.group(0)) == 32 else "id"
                names[m.group(0)] = f"{kind}-{sum(1 for v in names.values() if v.startswith(kind)) + 1}"
            return names[m.group(0)]
        return pattern.sub(name, s)

    def walk(v):
        if isinstance(v, dict):
            return {k: (PAGE_CID if k == "conversation_id" else walk(x)) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return text(v) if isinstance(v, str) else v
    return walk(value)


def record(request, monkeypatch, tmp_path):
    out = []
    for host in ("MC", "CoS"):
        for name, scenario in SCENARIOS.items():
            h = driver(host, request, monkeypatch, tmp_path / host / name)
            sid, steps = h.start(), []

            def step(route, body, response, lost=False):
                answer = response.get_json()
                steps.append({"route": route, "request": body, "status": response.status_code, "response": answer,
                              **({"lost": True} if lost else {})})
                return answer
            scenario(h, sid, step)
            out.append(normalized({"host": host, "scenario": name, "effects": h.effects(), "steps": steps}, sid))
    return out


def test_the_recording_is_the_real_servers_answers_now(request, monkeypatch, tmp_path):
    got = record(request, monkeypatch, tmp_path)
    if os.environ.get("VOICE_RECORD") == "1":
        RECORDING.write_text(json.dumps({"about": __doc__.split("\n\n")[0], "scenarios": got}, indent=1,
                                        ensure_ascii=False) + "\n")
    kept = json.loads(RECORDING.read_text())["scenarios"]
    assert got == kept, "the server's answers changed: regenerate with VOICE_RECORD=1, then run the page rig"


@pytest.mark.parametrize("host", ["MC", "CoS"])
def test_the_recording_holds_what_the_page_cases_rely_on(host):
    """What the page's integrated cases assert against is in the recording itself (so a regenerated one that lost it
    fails here): M1's answer is the typed turn's, with no saved flag and kind consult; S3's again carries the action's
    own owner item and hash, and its one confirmation saved the note once."""
    kept = {s["scenario"]: s for s in json.loads(RECORDING.read_text())["scenarios"] if s["host"] == host}
    m1 = kept["m1_natural_note_is_his_typed_turn"]
    ask, done = m1["steps"][0]["response"], m1["steps"][-1]["response"]
    assert ask["kind"] == "consult" and ask["heard"] == "note"
    assert done["outcome"] == "done" and "saved" not in done and done["spoken"] not in ("Saved.", "Post-it added.")
    assert m1["effects"] == 1
    s3 = kept["s3_second_yes_after_a_lost_answer"]
    lost, again, done = s3["steps"][2], s3["steps"][3]["response"], s3["steps"][-1]["response"]
    assert lost["lost"] is True and lost["response"]["decision"] == "confirm"
    assert again["again"] is True and again["owner_item_id"] == "t0" and again["confirm_item_id"] == "t2"
    assert again["text_sha256"] == s3["steps"][0]["response"]["text_sha256"]
    assert done["outcome"] == "done" and done["saved"] is True and s3["effects"] == 1
