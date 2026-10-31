"""What chunk A3 needs from the direct-voice host core for Confer's reveal (Spec 165 §6.6), through the fake adapter of
test_direct_host.py with a store that lists its sessions (test_direct_host_a2.py's Listing): End keeps why each turn was
not acted on (Gate 1 n2, n3); a session's detail gives each record's role and capture id, the turns not acted on with
why, and the summary proposal's words; and on a provider that folds an overtaken turn into the next one (xAI, live probe
LV6) the summary prompt says words may repeat. MC is unchanged: its store lists nothing, so it gets no detail route, and
its End's not-acted record only gains a field when its page names a reason. No provider, no Keychain, no network."""
from core.realtime_voice import direct_host as core

from test_direct_host import BASE, OWNER, post, start, turn
from test_direct_host_a2 import Listing, build


class Revealing(Listing):
    """The listing fake, keeping each record's role and capture id as CoS's file store does (turns())."""

    def turns(self, handle, sid):
        from core.realtime_voice.transcripts import SPEAKERS
        return ([{"role": r["role"], "item_id": r["key"][2], "speaker": SPEAKERS[r["role"]], "text": r["text"],
                  "partial": r["partial"], "interrupted": r["interrupted"]} for r in self.rows if r["sid"] == sid], True)


def end(r, sid, **body):
    return r.client.post(BASE + f"/session/{sid}/end", headers=OWNER, json={"last_item_id": "t2", "unacknowledged": [], **body})


def detail(r, sid):
    answer = r.client.get(BASE + f"/sessions/{sid}?conversation_id=owner", headers=OWNER)
    assert answer.status_code == 200
    return answer.get_json()


# ── n2, n3: why each turn was not acted on ───────────────────────────────────────────────────────────────────────────

def test_end_keeps_why_each_turn_was_not_acted_on_and_the_detail_says_it(monkeypatch, tmp_path):
    r = build(monkeypatch, tmp_path, store=Revealing())
    sid = start(r)
    post(r, sid, [turn(r, "user", "t0", "Save a note that A."), turn(r, "user", "t1", "Save a note that B."),
                  turn(r, "user", "t2", "Check the board.")])
    end(r, sid, not_acted=["t0", "t1", "t2"], not_acted_why={"t0": "cut_short", "t1": "overtaken", "t2": "closed"})
    kept = r.store.get("owner", sid, core.NOT_ACTED_RECORD)
    assert kept["owner_item_ids"] == ["t0", "t1", "t2"] and kept["why"] == {"t0": "cut_short", "t1": "overtaken", "t2": "closed"}
    assert detail(r, sid)["not_acted"] == {"t0": "cut_short", "t1": "overtaken", "t2": "closed"}


def test_a_reason_the_core_does_not_know_or_for_a_turn_not_named_is_dropped(monkeypatch, tmp_path):
    r = build(monkeypatch, tmp_path, store=Revealing())
    sid = start(r)
    post(r, sid, [turn(r, "user", "t0", "Save a note that A."), turn(r, "user", "t1", "Save a note that B.")])
    end(r, sid, not_acted=["t0", "t1", "x9"], not_acted_why={"t0": "because", "t1": "never_started", "t5": "closed",
                                                             "t0x": ["closed"]})
    kept = r.store.get("owner", sid, core.NOT_ACTED_RECORD)
    assert kept["owner_item_ids"] == ["t0", "t1"] and kept["why"] == {"t1": "never_started"}
    assert detail(r, sid)["not_acted"] == {"t0": None, "t1": "never_started"}


def test_a_page_that_names_no_reason_keeps_the_record_it_kept_before(monkeypatch, tmp_path):
    r = build(monkeypatch, tmp_path, store=Revealing())
    sid = start(r)
    end(r, sid, not_acted=["t0"])
    kept = r.store.get("owner", sid, core.NOT_ACTED_RECORD)
    assert set(kept) == {"schema", "id", "kind", "at", "owner_item_ids"}           # as at 99afd1ee: no why field
    assert detail(r, sid)["not_acted"] == {"t0": None}
    other = start(r)
    end(r, other, not_acted_why={"t0": "closed"})                                   # a reason with no list: nothing kept
    assert r.store.get("owner", other, core.NOT_ACTED_RECORD) is None and detail(r, other)["not_acted"] == {}


# ── the detail, for the reveal ───────────────────────────────────────────────────────────────────────────────────────

def test_the_detail_gives_each_record_its_role_and_capture_id(monkeypatch, tmp_path):
    r = build(monkeypatch, tmp_path, store=Revealing())
    sid = start(r)
    post(r, sid, [turn(r, "user", "t0", "Check the board."), turn(r, "assistant", "t1", "Two items", partial=True)])
    end(r, sid)
    records = detail(r, sid)["records"]
    assert [(x["role"], x["item_id"], x["text"], x["partial"]) for x in records] == [
        ("user", "t0", "Check the board.", False), ("assistant", "t1", "Two items", True)]
    assert "proposal" not in detail(r, sid)


def test_a_store_without_turns_still_reveals_its_records_with_no_marks(monkeypatch, tmp_path):
    r = build(monkeypatch, tmp_path, store=Listing())                               # the hook's default: records()
    sid = start(r)
    post(r, sid, [turn(r, "user", "t0", "Check the board.")])
    end(r, sid)
    (only,) = detail(r, sid)["records"]
    assert only["text"] == "Check the board." and only["role"] is None and only["item_id"] is None


def test_the_detail_carries_the_summary_proposal_for_the_card(monkeypatch, tmp_path):
    r = build(monkeypatch, tmp_path, store=Revealing())
    sid = start(r)
    post(r, sid, [turn(r, "user", "t0", "We looked at #12. We're done.")])
    r.client.post(BASE + f"/session/{sid}/summary", headers=OWNER, json={"conversation_id": "owner"})
    body = detail(r, sid)
    assert body["summary"] == "proposed" and body["proposal"] == r.store.summary("owner", sid)
    assert body["proposal"].startswith(core.PROPOSAL_HEAD)


# ── LV6: on a provider that folds an overtaken turn into the next, the summary prompt says words may repeat ─────────

def test_the_summary_prompt_says_words_may_repeat_on_a_provider_that_folds_turns(monkeypatch, tmp_path):
    r = build(monkeypatch, tmp_path, store=Revealing())
    prompts = {}
    for provider in ("openai", "xai"):
        sid = start(r, provider=provider)
        assert r.sessions.provider(sid) == provider
        post(r, sid, [turn(r, "user", "t0", "Name an animal."), turn(r, "user", "t1", "Name an animal. Trees grow tall.")])
        r.client.post(BASE + f"/session/{sid}/summary", headers=OWNER, json={"conversation_id": "owner"})
        prompts[provider] = r.host.prompts[-1]
    assert "Words may repeat across turns on this provider" in prompts["xai"]
    assert "Words may repeat" not in prompts["openai"]
    assert "Name an animal.\n" in prompts["xai"] and "Name an animal. Trees grow tall." in prompts["xai"]   # kept as said


def test_a_session_this_process_did_not_start_is_summarized_without_the_provider_sentence(monkeypatch, tmp_path):
    prompt, *_ = core.summary_prompt([("owner ASR", "Name an animal.", False, False)], True)
    assert "Words may repeat" not in prompt
    assert core.FOLDS_OVERTAKEN == ("xai",)
    r = build(monkeypatch, tmp_path, store=Revealing())
    assert r.sessions.provider("7c1d2e3f-4a5b-4c6d-8e9f-0a1b2c3d4e5f") is None
