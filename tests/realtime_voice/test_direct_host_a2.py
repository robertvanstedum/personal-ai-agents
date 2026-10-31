"""What chunk A2 adds to the direct-voice host core for CoS, through the fake adapter of test_direct_host.py (not the Guild,
not CoS): a bilingual card (Spec 165 §7, D19 (a)); admission apart from install (§6.8, §11.1, D11 (a)); a host that takes
no provider default for a voice its card does not name (D2 (a)); the store's close, state and sessions (§4.4), with the
needs-attention list, detail, download and dismissal for a store that lists its sessions (§6.5, D7); and a session text
refused with its reason (§6.3). MC's behaviour is unchanged: its store lists nothing, so it gets no new route. No provider,
no Keychain, no microphone, no network."""
import json
import re
import threading
import time

import pytest
from flask import Flask

from core.realtime_voice import direct_host as core
from core.realtime_voice.budget import VoiceBudget
from core.realtime_voice.direct_host import NOT_ON, DirectVoiceStore, install_direct_voice, lists_sessions
from core.realtime_voice.errors import VoiceUnavailable
from core.realtime_voice.standards import BILINGUAL_TRANSCRIPTION_HINT

import test_direct_host as fake
from test_direct_host import BASE, ENV, OWNER, Gateway, Host, Store, card_release, post, start, turn


class Listing(Store):
    """The fake store, listing its sessions (as CoS's file store does): state() and sessions() from what it keeps."""

    def __init__(self):
        super().__init__()
        self.damaged = set()

    def sessions(self, owner, needing_attention=True):
        sids = [sid for (sid, what), m in self.markers.items() if what == "start" and m["principal"] == owner]
        return [("owner", sid) for sid in reversed(sids)]

    def state(self, handle, sid):
        start = self.markers.get((sid, "start"))
        if start is None:
            return None
        kept = {rid: rec for (s, rid), rec in self.kept_records.items() if s == sid}
        return {"start": start, "end": self.markers.get((sid, "end")), "summary": self.summary_state(handle, sid),
                "requests": [r["owner_item_id"] for r in kept.values() if r["kind"] == core.REQUEST],
                "outcomes": {r["owner_item_id"]: r["outcome"] for r in kept.values() if r["kind"] == core.OUTCOME},
                "damaged": sid in self.damaged, "dismissed": core.DISMISSED_RECORD in kept}


def build(monkeypatch, tmp_path, *, card=None, store=None, may_start=None, default_voices=True, environ=None):
    card_release(tmp_path / "card", "1.0.0", card or {"card": "Test Person, plain and brief.", "language": "en-US",
                                                       "voices": {"openai": "cedar"}})
    app, host, mints, clock = Flask("direct-a2"), Host(tmp_path / "card"), [], [time.time()]
    if store is not None:
        host.store = store
    host.default_voices = default_voices

    def mint(**kw):
        mints.append(kw)
        return {"provider": "openai", "client_secret": "ek_synthetic", "expires_at": 1, "model": "gpt-realtime-2.1"}

    def xai(**kw):
        mints.append(kw)
        return {"provider": "xai", "ephemeral_token": "t", "expires_after_seconds": 300, "model": "grok-voice-latest",
                "session_config": {}}
    monkeypatch.setattr("core.realtime_voice.bootstrap.openai_realtime.mint_ephemeral_credential", mint)
    monkeypatch.setattr("core.realtime_voice.bootstrap.xai_voice.mint_ephemeral_credential", xai)
    monkeypatch.setattr("core.realtime_voice.bootstrap._rate_limit_state", {})
    budget = VoiceBudget("sqlite:///" + str(tmp_path / "budget.db"), cap_usd=lambda: 20)
    budget.ensure_schema()
    gateway = Gateway(host)
    metadata, sessions = install_direct_voice(app, host, url_prefix="/tp/voice", environ=dict(ENV if environ is None else environ),
                                              budget=budget, clock=lambda: clock[0], summary_http=gateway,
                                              may_start=may_start)
    from types import SimpleNamespace
    return SimpleNamespace(app=app, client=app.test_client(), host=host, mints=mints, clock=clock, budget=budget,
                           metadata=metadata, sessions=sessions, store=host.store, gateway=gateway)


def boot(r, provider="openai"):
    return r.client.post(BASE + "/bootstrap", headers=OWNER, json={"persona": "test_person", "provider": provider,
                                                                    "conversation_id": "owner"})


def reserved(r):
    return r.budget.status()["reserved_usd"]


# ── D19 (a): a bilingual card ────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("provider", ["openai", "xai"])
def test_a_bilingual_card_is_transcribed_in_the_language_spoken(monkeypatch, tmp_path, provider):
    r = build(monkeypatch, tmp_path, card={"card": "Test Person.", "transcription": "bilingual",
                                           "voices": {"openai": "cedar", "xai": "ara"}})
    assert boot(r, provider).status_code == 200
    (minted,) = r.mints
    assert minted["transcription_language"] is None
    assert minted.get("transcription_prompt") == (BILINGUAL_TRANSCRIPTION_HINT if provider == "openai" else None)


def test_a_card_without_transcription_keeps_its_locales_one_language(monkeypatch, tmp_path):
    r = build(monkeypatch, tmp_path)
    assert boot(r).status_code == 200
    assert r.mints[0]["transcription_language"] == "en" and "transcription_prompt" not in r.mints[0]


# ── D11 (a): installed is not admitted ───────────────────────────────────────────────────────────────────────────────

def test_a_start_not_admitted_is_refused_before_anything_is_recorded_or_reserved(monkeypatch, tmp_path):
    allowed = [False]
    r = build(monkeypatch, tmp_path, may_start=lambda: allowed[0])
    refused = boot(r)
    assert refused.status_code == 503 and refused.get_json()["error"] == NOT_ON == refused.get_json()["message"]
    assert r.mints == [] and r.store.markers == {} and reserved(r) == 0
    allowed[0] = True
    sid = boot(r).get_json()["voice_session_id"]
    allowed[0] = False                                  # admission turned off with a session open (S3)
    assert post(r, sid, [turn(r, "user", "t0", "Kept words.")]).status_code == 200
    ended = r.client.post(BASE + f"/session/{sid}/end", headers=OWNER, json={})
    assert ended.status_code == 200 and ended.get_json()["ended"] is True      # the record routes still answer


def test_admission_refused_with_the_hosts_own_words(monkeypatch, tmp_path):
    words = "Voice unavailable: there is no place to keep what is said."
    r = build(monkeypatch, tmp_path, may_start=lambda: words)
    assert boot(r).get_json() == {"error": words, "message": words}


def test_a_host_with_no_admission_rule_starts_as_before(monkeypatch, tmp_path):
    assert boot(build(monkeypatch, tmp_path)).status_code == 200


# ── D2 (a): no fallback to a provider default where the host takes none ──────────────────────────────────────────────

def test_a_provider_the_card_names_no_voice_for_is_refused_where_the_host_takes_no_default(monkeypatch, tmp_path):
    r = build(monkeypatch, tmp_path, default_voices=False)
    refused = boot(r, "xai")
    assert refused.status_code == 503
    assert refused.get_json()["error"] == "Voice unavailable: no Grok voice is chosen for Test Person yet."
    assert r.mints == [] and r.store.markers == {} and reserved(r) == 0
    assert r.metadata["unvoiced"] == {"xai": "Voice unavailable: no Grok voice is chosen for Test Person yet."}
    assert boot(r, "openai").status_code == 200 and r.mints[0]["voice"] == "cedar"


def test_a_host_that_takes_defaults_still_speaks_in_them_and_says_so(monkeypatch, tmp_path):
    r = build(monkeypatch, tmp_path)                    # MC's rule: the default, said in a notice
    answer = boot(r, "xai")
    assert answer.status_code == 200 and r.mints[0]["voice"] == "eve"
    assert answer.get_json()["voice_notices"] == ["xai voice is unset, using eve"] and "unvoiced" not in r.metadata


# ── §6.3: a session text refused with its reason ─────────────────────────────────────────────────────────────────────

def test_a_session_text_the_host_refuses_says_why_and_nothing_is_recorded(monkeypatch, tmp_path):
    r = build(monkeypatch, tmp_path)
    words = "Voice unavailable: her briefing does not fit one voice session."

    def too_big(card, handle, actions=True, page_asks=False):      # adapted for the v3 fix round (S6: page_asks)
        raise VoiceUnavailable(words)
    monkeypatch.setattr(r.host, "instructions", too_big)
    refused = boot(r)
    assert refused.status_code == 503 and refused.get_json()["error"] == words
    assert r.mints == [] and r.store.markers == {} and reserved(r) == 0


# ── §4.4: close, once ────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_second_end_answers_what_the_first_close_kept(monkeypatch, tmp_path):
    r = build(monkeypatch, tmp_path)
    sid = start(r)
    first = r.client.post(BASE + f"/session/{sid}/end", headers=OWNER, json={"last_item_id": "t1", "unacknowledged": ["t1"]})
    again = r.client.post(BASE + f"/session/{sid}/end", headers=OWNER, json={"last_item_id": "t1", "unacknowledged": []})
    assert first.get_json() == again.get_json() == {"ok": True, "ended": True, "capture": "incomplete", "missing": 1}
    assert r.host.ends == [sid]                          # the host hears of the close once


def test_the_store_defaults_are_built_from_the_hooks_mc_already_has(monkeypatch, tmp_path):
    store = Store()
    assert store.close("owner", "s", reason="end", capture={"capture": "complete"})[0] == "closed"
    assert store.close("owner", "s", reason="end", capture={"capture": "incomplete", "missing": 2}) == (
        "already", store.markers[("s", "end")])
    assert store.state("owner", "nope") is None and store.sessions("robert") is None
    assert not lists_sessions(store) and lists_sessions(Listing()) and not lists_sessions(None)


def test_a_store_that_lists_nothing_gets_no_record_route(monkeypatch, tmp_path):
    r = build(monkeypatch, tmp_path)
    assert "sessions_url" not in r.metadata and "capabilities_url" not in r.metadata
    assert r.client.get(BASE + "/sessions", headers=OWNER).status_code == 404
    assert r.client.get(BASE + "/capabilities", headers=OWNER).status_code == 404


# ── §6.5: needs attention, detail, download, dismissal (a store that lists its sessions) ─────────────────────────────

def listed(r):
    answer = r.client.get(BASE + "/sessions", headers=OWNER)
    assert answer.status_code == 200 and answer.headers["Cache-Control"] == "no-store"
    return {v["session_id"]: v for v in answer.get_json()["sessions"]}


def test_the_list_shows_what_needs_attention_and_never_a_live_session(monkeypatch, tmp_path):
    r = build(monkeypatch, tmp_path, store=Listing())
    live = start(r)
    post(r, live, [turn(r, "user", "t0", "Still talking.")])
    assert listed(r) == {}                               # live: not yet
    ended = start(r)
    r.client.post(BASE + f"/session/{ended}/end", headers=OWNER, json={"last_item_id": "t1", "unacknowledged": ["t1"]})
    clean = start(r)
    post(r, clean, [turn(r, "user", "t0", "All kept.")])
    r.client.post(BASE + f"/session/{clean}/end", headers=OWNER, json={"last_item_id": "t0", "unacknowledged": []})
    views = listed(r)
    assert set(views) == {ended} and views[ended]["reasons"] == ["incomplete"] and views[ended]["missing"] == 1
    r.clock[0] += 3 * 3600                               # past every maximum duration: the open one dropped
    views = listed(r)
    assert views[live]["state"] == "dropped" and views[live]["capture"] == "unconfirmed" and clean not in views


def test_an_undecided_or_unavailable_summary_and_an_unknown_consult_need_attention_until_decided(monkeypatch, tmp_path):
    r = build(monkeypatch, tmp_path, store=Listing())
    sid = start(r)
    post(r, sid, [turn(r, "user", "t0", "We looked at #12. We're done.")])
    r.client.post(BASE + f"/session/{sid}/summary", headers=OWNER, json={"conversation_id": "owner"})
    assert listed(r)[sid]["reasons"] == ["undecided"]
    r.client.post(BASE + f"/session/{sid}/summary/decision", headers=OWNER, json={"conversation_id": "owner", "decision": "confirm"})
    assert sid not in listed(r)
    other = start(r)
    r.store.put("owner", other, core.record(core.REQUEST, core.request_id("t3"), owner_item_id="t3", text_sha256="0" * 64))
    r.client.post(BASE + f"/session/{other}/end", headers=OWNER, json={"last_item_id": "t3", "unacknowledged": []})
    view = listed(r)[other]
    assert view["reasons"] == ["unknown_consult"] and view["consults"] == {"asked": 1, "pending": 0, "unknown": 1}
    r.host.model = ""                                    # the gateway answers 503: unavailable
    r.client.post(BASE + f"/session/{other}/summary", headers=OWNER, json={"conversation_id": "owner"})
    assert set(listed(r)[other]["reasons"]) == {"unknown_consult", "summary_unavailable"}


def test_the_detail_shows_only_state_while_live_and_the_records_after_end(monkeypatch, tmp_path):
    r = build(monkeypatch, tmp_path, store=Listing())
    sid = start(r)
    post(r, sid, [turn(r, "user", "t0", "Private until End."), turn(r, "assistant", "a0", "Half a rep", partial=True)])
    detail = r.client.get(BASE + f"/sessions/{sid}?conversation_id=owner", headers=OWNER)
    assert detail.get_json() == {"ok": True, "session_id": sid, "state": "live"} and "Private" not in detail.get_data(True)
    assert r.client.get(BASE + f"/sessions/{sid}/transcript?conversation_id=owner", headers=OWNER).status_code == 409
    r.client.post(BASE + f"/session/{sid}/end", headers=OWNER, json={"last_item_id": "a0", "unacknowledged": []})
    body = r.client.get(BASE + f"/sessions/{sid}?conversation_id=owner", headers=OWNER).get_json()
    assert body["state"] == "ended" and [x["text"] for x in body["records"]] == ["Private until End.", "Half a rep"]
    assert body["records"][1]["partial"] is True


def test_the_download_is_the_kept_records_with_partial_capture_labelled(monkeypatch, tmp_path):
    r = build(monkeypatch, tmp_path, store=Listing())
    sid = start(r)
    post(r, sid, [turn(r, "user", "t0", "Check the board."), turn(r, "assistant", "a0", "Two items", partial=True)])
    r.client.post(BASE + f"/session/{sid}/end", headers=OWNER, json={"last_item_id": "t1", "unacknowledged": ["t1"]})
    got = r.client.get(BASE + f"/sessions/{sid}/transcript?conversation_id=owner", headers=OWNER)
    assert got.status_code == 200 and got.mimetype == "text/plain" and got.headers["Cache-Control"] == "no-store"
    assert got.headers["Content-Disposition"].startswith('attachment; filename="voice-tp-')
    text = got.get_data(True)
    assert "Capture: incomplete: 1 turn not kept." in text
    assert "Rosa (speech recognition): Check the board." in text and "Test Person (partial): Two items" in text


def test_dismissing_takes_a_session_off_the_list_and_only_the_owner_reads_it(monkeypatch, tmp_path):
    r = build(monkeypatch, tmp_path, store=Listing())
    sid = start(r)
    r.client.post(BASE + f"/session/{sid}/end", headers=OWNER, json={"last_item_id": "t0", "unacknowledged": ["t0"]})
    stranger = {**OWNER, "X-Owner": "mallory"}
    assert r.client.get(BASE + f"/sessions/{sid}?conversation_id=owner", headers=stranger).status_code == 403
    assert r.client.get(BASE + "/sessions", headers=stranger).status_code == 401
    assert r.client.post(BASE + f"/sessions/{sid}/dismiss", headers=stranger, json={"conversation_id": "owner"}).status_code == 403
    gone = r.client.post(BASE + f"/sessions/{sid}/dismiss", headers=OWNER, json={"conversation_id": "owner"})
    assert gone.get_json() == {"ok": True, "dismissed": True, "repeated": False} and listed(r) == {}
    assert "dismiss" in r.host.kinds                     # the host's guard was asked for this POST by its own kind


def test_a_damaged_session_says_so_in_the_list_and_offers_no_download(monkeypatch, tmp_path):
    store = Listing()
    r = build(monkeypatch, tmp_path, store=store)
    sid = start(r)
    r.client.post(BASE + f"/session/{sid}/end", headers=OWNER, json={})
    store.damaged.add(sid)
    view = listed(r)[sid]
    assert view["line"] == "Voice records damaged" and view["reasons"][0] == "damaged"
    assert r.client.get(BASE + f"/sessions/{sid}/transcript?conversation_id=owner", headers=OWNER).status_code == 409


def test_the_capabilities_read_says_whether_a_start_is_allowed_and_whether_anything_needs_attention(monkeypatch, tmp_path):
    allowed = [True]
    r = build(monkeypatch, tmp_path, store=Listing(), may_start=lambda: allowed[0])
    read = lambda: r.client.get(BASE + "/capabilities", headers=OWNER).get_json()      # noqa: E731
    assert read()["direct_voice"] is True and read()["direct_records"] is False
    sid = start(r)
    r.client.post(BASE + f"/session/{sid}/end", headers=OWNER, json={"last_item_id": "t0", "unacknowledged": ["t0"]})
    allowed[0] = False
    caps = read()
    assert caps["direct_voice"] is False and caps["direct_records"] is True
    assert caps["sessions_url"] == "/tp/voice/api/realtime-voice/sessions" and caps["persona"] == "test_person"
    assert r.client.get(BASE + "/capabilities", headers={"Content-Type": "application/json"}).status_code == 401
    no_allowance = build(monkeypatch, tmp_path / "b", store=Listing(), environ={})
    assert no_allowance.client.get(BASE + "/capabilities", headers=OWNER).get_json()["direct_voice"] is False


@pytest.mark.parametrize("provider,locale,language", [("openai", "de-AT", "de"), ("xai", "de-AT", "de"),
                                                      ("openai", "pt-BR", "pt"), ("xai", "pt-BR", "pt-BR")])
def test_german_and_portuguese_mint_with_exactly_the_arguments_they_had(monkeypatch, provider, locale, language):
    """German and Portuguese personas carry no "transcription": their mint is called with the same keyword arguments as
    at b0c3518c (no transcription_prompt; their locale's one language)."""
    from core.realtime_voice.bootstrap import create_bootstrap_blueprint
    mints = []
    monkeypatch.setattr(f"core.realtime_voice.bootstrap.{'openai_realtime' if provider == 'openai' else 'xai_voice'}"
                        ".mint_ephemeral_credential", lambda **kw: mints.append(kw) or {"provider": provider, "model": "m"})
    monkeypatch.setattr("core.realtime_voice.bootstrap._rate_limit_state", {})
    app = Flask("lang")
    app.register_blueprint(create_bootstrap_blueprint(
        domain="german", locale=locale, is_production=lambda: True, owner=lambda: ("robert", "Robert"),
        get_persona=lambda name: {"name": "Frau Berger", "prompt_txt": "x.", "scenes": {}, "voices": {}}))
    assert app.test_client().post("/api/realtime-voice/bootstrap", json={"persona": "f", "provider": provider}).status_code == 200
    expected = {"instructions", "voice", "turn_detection", "transcription_language", "tools"} | (
        {"user_id_for_safety_identifier"} if provider == "openai" else set())
    assert set(mints[0]) == expected and mints[0]["transcription_language"] == language


# ── Gate 2 Q S1, Q S2 ────────────────────────────────────────────────────────────────────────────────────────────────

def test_an_older_session_that_needs_a_look_stays_listed_behind_fifty_clean_newer_ones(monkeypatch, tmp_path):
    """Q S1: the list was cut to 50 before it was filtered, so 50 newer sessions that need nothing pushed an older one with
    an unknown consult off it for good. Filtered first, then bounded: it stays, and the capabilities read says so."""
    from datetime import datetime, timezone
    store = Listing()
    r = build(monkeypatch, tmp_path, store=store)
    when = core.stamp(datetime.now(timezone.utc))

    def session(sid):
        store.markers[(sid, "start")] = {"principal": "robert", "conversation_id": "owner", "max_minutes": 30,
                                         "started_at": when}
        store.markers[(sid, "end")] = {"reason": "end", "ended_at": when, "capture": "complete"}
    old = "00000000-0000-4000-8000-000000000000"
    session(old)
    store.put("owner", old, core.record(core.REQUEST, core.request_id("t0"), owner_item_id="t0", text_sha256="a" * 64))
    for n in range(1, 51):                                   # 50 newer, each ended cleanly with no summary: nothing to look at
        session(f"00000000-0000-4000-8000-{n:012d}")
    views = listed(r)
    assert list(views) == [old] and views[old]["reasons"] == ["unknown_consult"]
    assert r.client.get(BASE + "/capabilities", headers=OWNER).get_json()["direct_records"] is True
    for n in range(51, 101):                                 # and the bound still holds for sessions that do need a look
        sid = f"00000000-0000-4000-8000-{n:012d}"
        session(sid)
        store.put("owner", sid, core.record(core.REQUEST, core.request_id("t0"), owner_item_id="t0", text_sha256="b" * 64))
    views = listed(r)
    assert len(views) == core.SESSIONS_MAX and old not in views


def test_admitted_but_refused_is_told_apart_from_not_admitted_with_its_words(monkeypatch, tmp_path):
    """Q S2: the capabilities answer says when she is admitted but a start is refused (the host's own words, no allowance
    set here), so the page says "Voice unavailable: <reason>" and offers no other mode; not admitted says nothing."""
    words = "Voice unavailable: there is no place to keep what is said."
    allowed = [words]
    r = build(monkeypatch, tmp_path, store=Listing(), may_start=lambda: allowed[0])
    read = lambda: r.client.get(BASE + "/capabilities", headers=OWNER).get_json()      # noqa: E731
    assert read()["direct_voice"] is False and read()["direct_refused"] == words
    allowed[0] = False                                       # not admitted (S1, S3): today's modes, no words
    assert read()["direct_voice"] is False and "direct_refused" not in read()
    allowed[0] = True
    assert read()["direct_voice"] is True and "direct_refused" not in read()
    no_allowance = build(monkeypatch, tmp_path / "b", store=Listing(), environ={})
    caps = no_allowance.client.get(BASE + "/capabilities", headers=OWNER).get_json()
    assert caps["direct_voice"] is False and caps["direct_refused"] == core.NO_ALLOWANCE
    refused = boot(no_allowance)                             # a start is still refused, as before (MC's words unchanged)
    assert refused.status_code == 503 and refused.get_json() == {"error": "unavailable", "message": core.NO_ALLOWANCE}
