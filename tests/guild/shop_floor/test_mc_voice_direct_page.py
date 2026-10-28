"""The Guild page and MC voice on the direct path (CC_16): the direct script and the live controls are listed and served only
while the direct path is on; with it off the page has nothing new. A revealed session renders as spoken turns in the
server-rendered thread. Real portal (owner guard, CSRF, SQLite floor), a stubbed provider mint, the OpenClaw adapter over a
stubbed transport. No provider, no Keychain, no microphone, no network."""
import re
from datetime import datetime, timedelta, timezone

import pytest

from floor_helpers import load_portal, write_headers  # noqa:F401
from floor_db_helpers import attach, floor_db, keyed  # noqa:F401
from test_mc_voice_release_check import API, DIRECT, ON, consult, conversation, headers, portal_with

PAGE = "/guild-next/guild/build"
SCRIPT = "/guild-next/guild/ui-assets/js/mc_voice_direct.js"
NEW_MARKERS = ("data-mc-voice-live", "data-mc-voice-check", "data-mc-voice-end", "data-mc-voice-propose",
               "data-mc-voice-notice", "mc_voice_direct.js")
VOICE_ONLY = {k: v for k, v in ON.items() if k != "MINIMOI_MC_VOICE_DIRECT"}


def page(client, cid=None):
    response = client.get(PAGE + (f"?c={cid}" if cid else ""))
    assert response.status_code == 200
    return response.get_data(as_text=True)


@pytest.fixture
def released(load_portal, floor_db, monkeypatch, tmp_path):
    return portal_with(load_portal, floor_db, monkeypatch, tmp_path, ON)


def test_with_the_direct_path_on_the_page_lists_and_serves_its_script_and_live_controls(released):
    _portal, _runtime, _minted, client, _token = released
    html = page(client)
    preloads = re.findall(r'<link rel="modulepreload" href="([^"]+)"', html.split("</head>")[0])
    assert any(href.endswith("/js/mc_voice_direct.js") for href in preloads)
    for marker in NEW_MARKERS[:-1]:
        assert marker in html, marker
    live = re.search(r'<div class="mc-voice-live" data-mc-voice-live[^>]*>', html).group(0)
    assert " hidden" in live                                                  # nothing shows until a session is live
    assert ">Check with MC<" in html and ">End<" in html and ">Sum up<" in html and ">Keep talking<" in html
    served = client.get(SCRIPT)
    assert served.status_code == 200 and b"export function directVoice" in served.data


@pytest.mark.parametrize("env", [VOICE_ONLY, {}], ids=["voice-on-direct-off", "voice-off"])
def test_with_the_direct_path_off_the_page_has_nothing_new_and_the_script_is_not_served(
        load_portal, floor_db, monkeypatch, tmp_path, env):
    _portal, _runtime, _minted, client, _token = portal_with(load_portal, floor_db, monkeypatch, tmp_path, env)
    html = page(client)
    for marker in NEW_MARKERS:
        assert marker not in html, marker
    assert ('data-mc-voice="true"' in html) is (env.get("MINIMOI_MC_VOICE") == "1")
    assert client.get(SCRIPT).status_code == 404
    assert client.get("/guild-next/guild/ui-assets/js/mc_voice.js").status_code == 200    # the rest is served as before


def test_a_revealed_session_renders_as_spoken_turns_and_one_line_per_consult_in_the_server_rendered_thread(released):
    _portal, _runtime, _minted, client, token = released
    cid = conversation(client, token)
    sid = client.post(DIRECT + "/bootstrap", headers=headers(token),
                      json={"persona": "master_craftsman", "provider": "openai", "conversation_id": cid}).get_json()[
        "voice_session_id"]
    now = datetime.now(timezone.utc)
    stamp = lambda s: (now + timedelta(seconds=s)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"     # noqa: E731
    kept = client.post(DIRECT + f"/session/{sid}/transcript", headers=headers(token), json={"events": [
        {"role": "user", "item_id": "t0", "index": 0, "text": "What is stuck on #12?", "partial": False,
         "interrupted": False, "started_at": stamp(0), "ended_at": stamp(1)},
        {"role": "assistant", "item_id": "t1", "index": 0, "text": "Let me check", "partial": True,
         "interrupted": True, "started_at": stamp(1), "ended_at": stamp(2)}]})
    assert kept.status_code == 200, kept.get_json()
    assert consult(client, token, sid).get_json()["answered"] is True
    live = page(client, cid)
    assert "What is stuck on #12?" not in live and "Let me check" not in live           # withheld while live
    assert client.post(DIRECT + f"/session/{sid}/end", headers=headers(token)).get_json()["ended"] is True
    html = page(client, cid)
    owner = re.search(r'<li class="msg msg-voice msg-note"[^>]*data-voice-kind="owner_turn".*?</li>', html, re.S).group(0)
    assert 'data-author-kind="owner"' in owner and "You · spoken" in owner and "What is stuck on #12?" in owner
    said = re.search(r'<li [^>]*data-voice-kind="assistant_turn".*?</li>', html, re.S).group(0)
    assert "Master Craftsman · spoken" in said and "interrupted" in said and "Let me check" in said
    receipt = re.search(r'<li [^>]*data-voice-kind="consult_receipt".*?</li>', html, re.S).group(0)
    # UI review D1: "Checked with MC · answered", then what was spoken; the reply id only in the title and data attribute.
    assert "Checked with MC · answered" in receipt and "Item 12 is in build." in receipt
    ref = re.search(r'data-voice-ref="([^"]+)"', receipt).group(1)
    assert f'title="Reply {ref}"' in receipt
    visible = re.sub(r"<[^>]+>", "", receipt)
    assert ref not in visible and "Answered." not in visible and "Spoken:" not in visible
    for hidden in ("consult_note", "consult_request"):
        row = re.search(rf'<li [^>]*data-voice-kind="{hidden}"[^>]*>', html).group(0)
        assert row.endswith(" hidden>"), hidden
    reply = re.search(r'<li [^>]*data-voice-kind="consult_reply"[^>]*>', html).group(0)
    assert reply.endswith(" hidden>")                     # the full answer says no more than was spoken: not repeated
    assert "[Voice session]" not in html                  # markers are never shown
    notes = client.get(API + f"/notes?conversation={cid}").get_json()["notes"]
    assert [n["voice"]["full_differs"] for n in notes if n.get("voice", {}).get("kind") == "consult_reply"] == [False]


def test_a_full_answer_is_shown_only_when_it_says_more_than_was_spoken():
    from minimoi_portal.guild_ui.mc_voice_reveal import full_differs
    assert full_differs("Item 12 is in build.") is False
    assert full_differs("**Item 12** is in build.\n\nNothing else.") is False             # marks and breaks do not count
    assert full_differs("Run this:\n```\nmake index\n```") is True                        # a code block is not spoken
    assert full_differs("See [the ticket](https://example.invalid/12).") is True          # nor a link's address
    assert full_differs("word " * 1000) is True                                           # cut at the spoken limit (3,500)
