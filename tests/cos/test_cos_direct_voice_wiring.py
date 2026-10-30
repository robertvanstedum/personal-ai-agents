"""Chief of Staff's direct voice wired into cos-scheduler's real app (domains/cos/chief_of_staff.py; Spec 165 v0.3 §6.4, §6.8,
§12.1, §12.2): not admitted is today; installed, a consult is her real Confer turn (_process_confer_turn) with her real
note, reminder and file paths, all in temporary folders. The CoS app is imported with its scheduler stubbed and the
network refused; her runtime is a fake that records what reaches it. No provider, no Keychain, no microphone."""
from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock

import pytest

from core.realtime_voice import budget as B
from core.realtime_voice import direct_host as core
from domains.cos import claim_truth

PREFIX = "/api/realtime-voice/direct/api/realtime-voice"
OWNER = {"X-Minimoi-Auth-Id": "7", "X-Minimoi-User-Tier": "owner", "X-Minimoi-Voice-Write-Verified": "1",
         "Content-Type": "application/json"}


def load(monkeypatch, tmp_path, **env):
    for name in ("apscheduler", "apscheduler.schedulers", "apscheduler.schedulers.background",
                 "apscheduler.triggers", "apscheduler.triggers.cron", "apscheduler.triggers.interval"):
        monkeypatch.setitem(sys.modules, name, MagicMock())
    inbox, outbox, conversations, voice = (tmp_path / n for n in ("inbox", "outbox", "conversations", "cos_voice"))
    for folder in (inbox, outbox, conversations, voice):
        folder.mkdir(exist_ok=True)
    base = {"DATABASE_URL": "postgresql://u:p@127.0.0.1:1/none", "DISABLE_TELEGRAM_POLL": "1", "COS_BACKEND_TYPE": "grok",
            "MINIMOI_LIMITS_DIR": str(tmp_path / "limits"), "COS_REMINDERS_DIR": str(tmp_path / "reminders"),
            "COS_FILES_INBOX": str(inbox), "COS_FILES_OUTBOX": str(outbox), "COS_AGENT_INBOX": "/minimoi/cos/inbox",
            "COS_AGENT_OUTBOX": "/minimoi/cos/outbox", "COS_CONVERSATIONS_DIR": str(conversations),
            "COS_AGENT_CONVERSATIONS": "/minimoi/cos/conversations", "COS_VOICE_DIR": str(voice),
            B.ENV_DSN: f"sqlite:///{tmp_path / 'vb.db'}", core.ALLOWANCE_ENV: "2000000"}
    for name in ("MINIMOI_COS_MANAGED_REALTIME", "MINIMOI_COS_VOICE_DIRECT", "MINIMOI_COS_DIRECT_VOICE_PRODUCTION",
                 "MINIMOI_ROLE", "MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_COS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in {**base, **env}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(B, "_default", None)
    B.default_budget().ensure_schema()
    minted = []
    monkeypatch.setattr("core.realtime_voice.bootstrap.openai_realtime.mint_ephemeral_credential",
                        lambda **kw: minted.append(kw) or {"provider": "openai", "client_secret": "ek", "expires_at": 1,
                                                           "model": "gpt-realtime-2.1"})
    monkeypatch.setattr("core.realtime_voice.bootstrap._rate_limit_state", {})
    sys.modules.pop("domains.cos.chief_of_staff", None)
    import domains.cos.chief_of_staff as module

    def no_network(*args, **kwargs):
        raise AssertionError("unexpected network call")
    monkeypatch.setattr(module.requests, "post", no_network)
    monkeypatch.setattr(module.requests, "get", no_network)
    memory = tmp_path / "cos_memory.md"
    memory.write_text("## Standing\n[note] 2026-10-27: the kiln is new\n")
    monkeypatch.setattr(module, "COS_MEMORY_FILE", memory)
    sent = []

    class Backend:
        backend_label, model_label = "fake", "fake-model"
        reply, write, timeouts = "Here is what I found.", None, []

        def call_backend(self, prompt, context, tool_policy):
            from domains.cos.backends import openclaw_backend
            Backend.timeouts.append(openclaw_backend.turn_timeout_seconds.get())
            sent.append((prompt, context))
            if Backend.write:
                (outbox / Backend.write).write_text("# result\n")
            return Backend.reply
    monkeypatch.setattr(module, "_backend", Backend())
    module.test = type("T", (), {"sent": sent, "backend": Backend, "minted": minted, "memory": memory, "voice": voice,
                                 "conversations": conversations, "reminders": tmp_path / "reminders"})
    return module


@pytest.fixture
def off(monkeypatch, tmp_path):
    yield load(monkeypatch, tmp_path)
    sys.modules.pop("domains.cos.chief_of_staff", None)


@pytest.fixture
def on(monkeypatch, tmp_path):
    yield load(monkeypatch, tmp_path, MINIMOI_COS_VOICE_DIRECT="1", MINIMOI_ROLE="development")
    sys.modules.pop("domains.cos.chief_of_staff", None)


def start(cos):
    answer = cos.app.test_client().post(PREFIX + "/bootstrap", headers=OWNER, json={
        "persona": "chief_of_staff", "provider": "openai", "conversation_id": "owner"})
    assert answer.status_code == 200, answer.get_json()
    return answer.get_json()["voice_session_id"]


def ask(cos, sid, words, item="t1", name="consult", call="call_1"):
    # v3: the action path acts only on Check (call id "host-…") or a confirmed action, so the wiring consults as Check.
    return cos.app.test_client().post(PREFIX + "/consult", headers=OWNER, json={
        "session_id": sid, "call_id": call if call.startswith("host-") else "host-" + call, "name": name, "owner_transcript_item": words, "owner_item_id": item}).get_json()


def reminder_records(cos):
    path = cos.test.reminders / "reminders.json"
    return json.loads(path.read_text()).get("reminders", []) if path.exists() else []


# ── not admitted = today (§6.6, §11.1 S0/S1), by behaviour ──────────────────────────────────────────────────────────

def test_with_todays_flags_nothing_of_her_direct_voice_exists_and_confer_is_as_before(off):
    rules = [r.rule for r in off.app.url_map.iter_rules()]
    assert not [r for r in rules if "realtime-voice/direct" in r]
    assert off._cos_direct_voice is None
    client = off.app.test_client()
    assert client.post(PREFIX + "/bootstrap", headers=OWNER, json={}).status_code == 404
    assert client.get("/ui/confer").status_code == 200
    answer = client.post("/ui/send", json={"text": "What is stuck on #12?"})
    assert answer.status_code == 200 and answer.get_json()["reply"] == "Here is what I found."
    (prompt, context), = off.test.sent
    assert prompt == "What is stuck on #12?" and "voice-<YYYY-MM-DD>" not in context["system_prompt"]
    assert off.test.minted == [] and list(off.test.voice.iterdir()) == []


def test_her_typed_turn_service_takes_no_dispatch_callback_unless_asked(off):
    result = off._process_confer_turn("Hello", channel="html_text")
    assert result.reply == "Here is what I found." and len(off.test.sent) == 1


# ── installed: a consult is her real Confer turn ──────────────────────────────────────────────────────────────────────

def test_installed_her_routes_answer_and_a_consult_is_her_canonical_turn(on):
    assert on._cos_direct_voice["persona"] == "chief_of_staff" and on._cos_direct_voice["locale"] == "en-US"
    caps = on.app.test_client().get(PREFIX + "/capabilities", headers={**OWNER, "X-Minimoi-Voice-Write-Verified": ""})
    assert caps.get_json()["direct_voice"] is True and caps.headers["Cache-Control"] == "no-store"
    sid = start(on)
    answer = ask(on, sid, "What is stuck on #12?")
    assert answer["outcome"] == "done" and answer["spoken"] == "Here is what I found."
    (prompt, context), = on.test.sent
    assert prompt == "What is stuck on #12?" and context["confer"]["input_channel"] == "html_voice"
    assert context["confer"]["conversation_id"] == "owner"
    assert on.test.backend.timeouts == [120]                    # her runtime call keeps typed Confer's own bound (A2-D1)
    session = on.test.minted[-1]
    assert "Her briefing (DATA" in session["instructions"] and "the kiln is new" in session["instructions"]
    assert "voice-<YYYY-MM-DD>-<session>.jsonl" in on._workspace_note()      # her recall now names the projections


def test_a_note_by_voice_is_in_her_memory_at_once(on):
    """D21 (a)."""
    sid = start(on)
    answer = ask(on, sid, "Save a note that the glaze is ready", name="save_note")
    assert answer["spoken"] == "Saved." and "the glaze is ready" in on.test.memory.read_text()
    assert on.test.sent == []                                      # a platform note: never her runtime


def test_a_spoken_reminder_leaves_no_reminder_record_in_her_real_reminder_store(on):
    """§6.4 / §12.2: "Remind me at 5 pm to stretch." by voice, and /remind by voice, leave no reminder record."""
    on.test.backend.reply = "Done, I'll remind you at 5 pm to stretch."
    sid = start(on)
    spoken = ask(on, sid, "Remind me at 5 pm to stretch.")["spoken"]
    assert "remind you" not in spoken                              # claim_truth: no scheduling claim survives
    refused = ask(on, sid, "/remind 2026-12-01T17:00Z stretch", item="t2", call="call_2")
    assert refused["spoken"] == core.IN_WRITING
    assert reminder_records(on) == []


def test_new_by_voice_never_resets_her_runtime(on, monkeypatch):
    resets = []
    monkeypatch.setattr(on, "_reset_backend_conversation", lambda cid: resets.append(cid) or True)
    sid = start(on)
    assert ask(on, sid, "/new")["spoken"] == core.IN_WRITING and resets == [] and on.test.sent == []


def test_a_download_claim_needs_a_delivered_file_through_her_real_outbox(on):
    on.test.backend.reply = "Done. I saved it as a downloadable file."
    sid = start(on)
    assert core.speakable(claim_truth.FILE_NOTICE) in ask(on, sid, "Make me a file.")["spoken"]
    on.test.backend.write = "agencies.md"
    delivered = ask(on, sid, "Make me another file.", item="t2", call="call_2")
    assert delivered["files"] == ["agencies.md"] and core.speakable(claim_truth.FILE_NOTICE) not in delivered["spoken"]


def test_an_ended_session_is_projected_into_her_recall_folder(on):
    sid = start(on)
    client = on.app.test_client()
    client.post(PREFIX + f"/session/{sid}/transcript", headers=OWNER, json={"events": [
        {"role": "user", "item_id": "t0", "text": "The kiln is hot.", "started_at": core.stamp(core.now()),
         "ended_at": core.stamp(core.now())}]})
    assert [p.name for p in on.test.conversations.iterdir()] == []
    client.post(PREFIX + f"/session/{sid}/end", headers=OWNER, json={"last_item_id": "t0", "unacknowledged": []})
    (path,) = [p for p in on.test.conversations.iterdir() if p.name.startswith("voice-")]
    assert "The kiln is hot." in path.read_text()


def test_in_production_her_routes_are_installed_but_no_session_starts_without_her_production_switch(monkeypatch, tmp_path):
    cos = load(monkeypatch, tmp_path, MINIMOI_COS_VOICE_DIRECT="1", MINIMOI_ROLE="production")
    try:
        refused = cos.app.test_client().post(PREFIX + "/bootstrap", headers=OWNER, json={
            "persona": "chief_of_staff", "provider": "openai", "conversation_id": "owner"})
        assert refused.status_code == 503 and refused.get_json()["error"] == core.NOT_ON and cos.test.minted == []
        assert cos.app.test_client().get(PREFIX + "/sessions", headers=OWNER).status_code == 200
    finally:
        sys.modules.pop("domains.cos.chief_of_staff", None)


# ── A3: what Confer's page reads and loads (Spec 165 v0.3 §6.6) ──────────────────────────────────────────────────────

def test_with_todays_flags_the_page_reads_one_capabilities_answer_that_offers_nothing(off):
    client = off.app.test_client()
    assert client.get(PREFIX + "/capabilities", headers=OWNER).status_code == 404      # not installed: Confer as before
    page = client.get("/ui/confer").get_data(True)
    assert '<div id="voice-live" class="voice-live" role="group" aria-label="Voice conversation" hidden></div>' in page
    assert '<div id="voice-sessions" class="voice-sessions" aria-label="Voice sessions to look at" hidden></div>' in page
    assert "const DIRECT_CAPABILITIES='../api/realtime-voice/direct/api/realtime-voice/capabilities';" in page


def test_installed_the_capabilities_answer_has_what_confer_draws(on):
    caps = on.app.test_client().get(PREFIX + "/capabilities", headers=OWNER).get_json()
    assert caps["path"] == "direct" and caps["direct_voice"] is True and caps["direct_records"] is False
    assert caps["person"] == {"name": "Chief of Staff", "short": "CoS"} and caps["persona"] == "chief_of_staff"
    assert caps["model_actions"] == {"openai": False, "xai": False}                 # v3: no model action (adapted)
    assert caps["unvoiced"] == {}                                       # her card names both voices (0.1.3)
    for key in ("bootstrap_url", "end_url", "session_url", "sessions_url", "session_transcript_url", "session_dismiss_url",
                "summary_url", "summary_decision_url", "keep_talking_url", "consult_url", "transcript_url"):
        assert caps[key].startswith(PREFIX + "/"), key                     # her own paths: the page rebases them


def test_her_static_files_are_served_and_the_portal_rewrites_none_of_them(off):
    client = off.app.test_client()
    page = client.get("/static/confer-voice.js")
    assert page.status_code == 200 and "javascript" in page.mimetype and b"export function conferVoice" in page.data
    session = client.get("/static/realtime-voice/direct-voice-session.js")
    assert session.status_code == 200 and b"export function directVoice" in session.data
    from pathlib import Path
    from minimoi_portal.proxy import _rewrite_js
    root = Path(__file__).resolve().parents[2]
    template = (root / "domains/cos/templates/cos_ui.html").read_text()
    script = template.split('<script type="module">')[1].split("</script>")[0]
    for text in (page.get_data(True), session.get_data(True), script,
                 (root / "core/realtime_voice/static/realtime-voice-controller.js").read_text()):
        assert _rewrite_js(text, "/app/cos") == text        # the page's own rebase is the only prefixing: never doubled
