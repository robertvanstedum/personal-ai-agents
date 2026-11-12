"""#510: the CoS page has a Private (lock) control, as the Guildhall's 🔒.

A conversation is private from its creation and stays so (the backend writes the flag once), so the lock starts a new
private conversation and Exit Private starts a new one on the record. Voice and dictation are off in a private chat.
The page is rendered by the CoS app with its scheduler stubbed and the network refused (test_history_wiring's loader).
All content is synthetic.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from test_history_wiring import OWNER, _load

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = (ROOT / "domains/cos/templates/cos_ui.html").read_text()
SCRIPT = (ROOT / "domains/cos/static/cos-history-ui.js").read_text()
STYLE = (ROOT / "domains/cos/static/calm-confer.css").read_text()


@pytest.fixture
def cos(monkeypatch, tmp_path):
    yield _load(monkeypatch, tmp_path, history_on=True)
    sys.modules.pop("domains.cos.chief_of_staff", None)


def _between(text, start, end):
    a = text.index(start)
    return text[a:text.index(end, a)]


def test_the_rendered_page_has_the_lock_in_options_and_a_hidden_private_chip(cos):
    html = cos.app.test_client().get("/ui/confer", headers=OWNER).get_data(as_text=True)
    options = _between(html, 'class="cos-options-body"', "</details>")
    assert 'data-cos-private' in options and 'aria-label="Go Private"' in options and "🔒" in options
    assert 'aria-pressed="false"' in options.split("data-cos-private", 1)[1].split(">", 1)[0]
    chip = re.search(r'<p class="cos-private-chip"[^>]*>.*?</p>', html, re.S).group(0)
    assert " hidden" in chip.split(">", 1)[0] and 'role="status"' in chip
    assert "data-private-exit" in chip and "Exit Private: go back on the record" in chip


def test_the_chip_stays_in_view_on_a_phone():
    # conv-head is hidden below 640 px (founders.css), so the chip sits above the composer, not in the header.
    head = _between(TEMPLATE, '<div class="conv-head">', '<div class="confer-workspace">')
    assert "data-private-chip" not in head
    chat = _between(TEMPLATE, '<div class="confer-workspace">', 'class="input-row"')
    assert "data-private-chip" in chat
    phone = _between(STYLE, "@media(max-width:639px){\n  .founders-cos .cos-private-chip", "}\n}")
    assert "width:calc(100% - 20px)" in phone and "min-height:36px" in phone and "min-height:44px" in phone
    assert ".cos-private-chip[hidden]{display:none}" in STYLE


def test_the_wording_says_what_private_means_here():
    # CoS keeps a private chat in its history (history.py); it stays out of MC (mc_send.py), memory (memory_shelf/cos.py)
    # and proactive notes. So the page never claims "MiniMoi keeps nothing", as the Guildhall's off-record Private does.
    assert "MiniMoi keeps nothing" not in TEMPLATE and "MiniMoi keeps nothing" not in SCRIPT
    assert "Private · stays out of MC and memory" in TEMPLATE
    assert "Kept in your CoS history. Nothing from it goes to MC, to memory or to proactive notes." in TEMPLATE
    for words in ("Go Private: a new chat that CoS answers and keeps, but nothing from it goes to MC or to memory",
                  "Exit Private: go back on the record (starts a new chat)"):
        assert words in SCRIPT


def test_the_lock_uses_the_create_api_and_never_switches_a_conversation():
    start = _between(SCRIPT, "async function startConversation(isPrivate)", "document.querySelectorAll('[data-new-conversation]')")
    assert "{title:'New conversation',private:true}" in start
    assert "lock.addEventListener('click', () => startConversation(!isPrivateNow()))" in SCRIPT
    assert "exitPrivate.addEventListener('click', () => startConversation(false))" in SCRIPT
    assert "button.onclick=()=>startConversation(false)" in SCRIPT
    # No route changes the flag of an existing conversation.
    api = (ROOT / "domains/cos/history_api.py").read_text()
    assert api.count('body.get("private")') == 1 and "history.create(" in api


def test_dictation_is_off_in_a_private_conversation_like_voice():
    gate = _between(TEMPLATE, "function updateVoiceAvailability(){", "updateVoiceAvailability();\nasync function")
    assert "const dictationReady=availability.dictationReady&&document.body.dataset.historyPrivate!=='true';" in gate
    assert "dictation.setAvailable(availability.dictationReady)" not in gate
    assert gate.count("dictation.setAvailable(dictationReady)") == 5
    # Voice: the selected chat's flag still gates the mic.
    assert "window.cosVoiceConversationAllowed = !doc?.private && !doc?.read_only;" in SCRIPT
    assert "document.body.dataset.historyPrivate = String(Boolean(doc?.private));" in SCRIPT


def test_a_private_conversation_is_created_listed_selected_and_stays_private(cos):
    client = cos.app.test_client()
    made = client.post("/ui/history/conversations", headers=OWNER,
                       json={"title": "New conversation", "private": True, "select": True, "device": "d1"})
    assert made.status_code == 201 and made.json["private"] is True
    cid = made.json["conversation_id"]
    chosen = client.get("/ui/history/selection?device=d1", headers=OWNER).json
    assert chosen["conversation_id"] == cid and chosen["conversation"]["private"] is True
    listed = {c["conversation_id"]: c["private"] for c in client.get("/ui/history/conversations").json["items"]}
    assert listed[cid] is True
    # Only a JSON true makes a chat private; anything else is on the record.
    loose = client.post("/ui/history/conversations", headers=OWNER, json={"title": "x", "private": "yes"})
    assert loose.status_code == 201 and loose.json["private"] is False
    normal = client.post("/ui/history/conversations", headers=OWNER, json={"title": "New conversation"})
    assert normal.json["private"] is False


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
def test_the_private_control_script():
    run = subprocess.run(["node", str(ROOT / "tests/cos/cos_private_control.test.cjs")], cwd=ROOT,
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
def test_the_navigation_script_still_passes_with_the_shared_start():
    run = subprocess.run(["node", str(ROOT / "tests/cos/cos_history_navigation.test.cjs")], cwd=ROOT,
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stdout + run.stderr
