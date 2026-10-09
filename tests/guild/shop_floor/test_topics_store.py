"""The topic workshop store (Workshop W1): ids, paths, revisions, archive, comments, layout, journal, caps.
Pure files in a temp folder. No model, no network."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from minimoi_portal.guild_ui import topics as T


@pytest.fixture
def store(tmp_path):
    return T.TopicStore(str(tmp_path))


def _img(ext="png"):
    return SimpleNamespace(data=b"\x89PNG-sample", thumb=b"RIFFthumb", mime="image/png", ext=ext, width=390, height=780, sha256="a" * 64)


def _topic(store, title="Guild chat improvements"):
    return store.create_topic("robert", title, "Design and fixes")


def test_a_topic_has_a_server_made_id_and_files_are_private(store, tmp_path):
    t = _topic(store)
    assert T.TOPIC_RE.fullmatch(t["id"]) and t["owner"] == "robert" and t["archived"] is False
    path = tmp_path / "topics" / t["id"] / "topic.json"
    assert path.is_file() and (path.stat().st_mode & 0o777) == 0o600
    assert (tmp_path / "topics").stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("bad", ["", "../etc", "t-../../x", "t-ZZZZZZZZZZZZ", "t-1234", "i-0123456789ab", None])
def test_a_bad_topic_id_never_reaches_a_path(store, bad):
    with pytest.raises(T.TopicNotFound):
        store.get_topic(bad, "robert")


def test_another_owner_cannot_see_or_change_a_topic(store):
    t = _topic(store)
    with pytest.raises(T.TopicNotFound):
        store.get_topic(t["id"], "someone-else")
    with pytest.raises(T.TopicNotFound):
        store.add_item(t["id"], "someone-else", "note", "x", text="y")
    assert store.list_topics("someone-else") == []


def test_title_rules_and_search_by_words(store):
    a = _topic(store, "Guild chat improvements")
    store.create_topic("robert", "Files panel on phones")
    with pytest.raises(T.Refused):
        store.create_topic("robert", "   ")
    with pytest.raises(T.Refused):
        store.create_topic("robert", "x" * 121)
    assert [r["title"] for r in store.list_topics("robert", q="files")] == ["Files panel on phones"]
    assert [r["id"] for r in store.list_topics("robert", q="chat guild")] == [a["id"]]            # every word must match, any order
    store.add_item(a["id"], "robert", "note", "Phone keyboard", text="the composer hides")
    assert [r["id"] for r in store.list_topics("robert", q="keyboard")] == [a["id"]]             # item titles are searched too
    assert store.list_topics("robert", q="zzz") == []


def test_a_note_document_and_request_have_text_revisions_and_a_design_has_an_image(store, tmp_path):
    t = _topic(store)["id"]
    n = store.add_item(t, "robert", "note", "A thought", text="What should stay visible?")
    d = store.add_item(t, "robert", "document", "Spec", text="# Heading\n\nText")
    r = store.add_item(t, "robert", "request", "Review the Files layout", text="Please review revision B",
                       request={"to": "Codex", "included": "Files layout revision B"})
    g = store.add_item(t, "robert", "design", "Files panel · phone", image=_img())
    assert [x["kind"] for x in (n, d, r, g)] == ["note", "document", "request", "design"]
    assert r["request"] == {"to": "Codex", "stage": "queued", "stage_source": "set by the owner", "stage_at": r["request"]["stage_at"], "included": "Files layout revision B"}
    base = tmp_path / "topics" / t / "items"
    assert (base / d["id"] / "rev" / "A.md").read_text() == "# Heading\n\nText"
    assert (base / g["id"] / "rev" / "A.png").read_bytes() == b"\x89PNG-sample" and (base / g["id"] / "rev" / "A.thumb.webp").is_file()
    assert store.get_item(t, d["id"], "robert")["text"] == "# Heading\n\nText"
    assert store.get_item(t, g["id"], "robert")["text"] == ""
    with pytest.raises(T.Refused):
        store.add_item(t, "robert", "design", "no image")
    with pytest.raises(T.Refused):
        store.add_item(t, "robert", "note", "empty", text="  ")
    with pytest.raises(T.Refused):
        store.add_item(t, "robert", "bogus", "x", text="y")


def test_a_change_is_a_new_revision_and_the_old_one_is_kept_intact(store, tmp_path):
    t = _topic(store)["id"]
    d = store.add_item(t, "robert", "document", "Spec", text="one")
    d = store.add_revision(t, d["id"], "robert", text="two", by="Claude Code", note="after Robert's comment")
    d = store.add_revision(t, d["id"], "robert", text="three")
    assert [r["rev"] for r in d["revisions"]] == ["A", "B", "C"] and d["current_rev"] == "C"
    assert d["revisions"][1]["by"] == "Claude Code" and d["revisions"][1]["note"].startswith("after Robert")
    assert store.get_item(t, d["id"], "robert", rev="A")["text"] == "one"            # earlier revisions still read exactly as written
    assert store.get_item(t, d["id"], "robert")["text"] == "three"
    with pytest.raises(T.Refused):
        store.get_item(t, d["id"], "robert", rev="Z")


def test_files_are_never_replaced(store, tmp_path):
    t = _topic(store)["id"]
    d = store.add_item(t, "robert", "document", "Spec", text="one")
    path = tmp_path / "topics" / t / "items" / d["id"] / "rev" / "A.md"
    with pytest.raises(T.Refused) as e:
        store._write_bytes(str(path), b"overwrite", exclusive=True)
    assert e.value.status == 409 and path.read_text() == "one"


def test_put_away_and_bring_back_keep_everything_and_block_edits_while_away(store):
    t = _topic(store)["id"]
    d = store.add_item(t, "robert", "document", "Spec", text="one")
    c = store.add_comment(t, d["id"], "robert", "Keep this", anchor={"type": "block", "index": 0, "quote": "one"})
    away = store.set_archived(t, d["id"], "robert", True)
    assert away["archived"] is True and away["archived_at"]
    with pytest.raises(T.Refused):
        store.add_revision(t, d["id"], "robert", text="two")
    back = store.set_archived(t, d["id"], "robert", False)
    assert back["archived"] is False and back["current_rev"] == "A"
    assert [x["id"] for x in store.comments(t, d["id"], "robert")] == [c["id"]]               # the discussion was kept


def test_comments_are_anchored_to_a_revision_and_replies_inherit_it(store):
    t = _topic(store)["id"]
    d = store.add_item(t, "robert", "document", "Spec", text="one")
    store.add_revision(t, d["id"], "robert", text="two")
    c1 = store.add_comment(t, d["id"], "robert", "On the first revision", rev="A", anchor={"type": "block", "index": 2, "quote": "para"})
    assert c1["rev"] == "A" and c1["anchor"] == {"type": "block", "index": 2, "quote": "para"} and c1["by"] == {"kind": "owner", "name": "robert"}
    r = store.add_comment(t, d["id"], "robert", "A reply", reply_to=c1["id"], by={"kind": "agent", "name": "Claude Code"})
    assert r["rev"] == "A" and r["anchor"] == c1["anchor"] and r["reply_to"] == c1["id"]            # a reply stays on its parent's revision
    c2 = store.add_comment(t, d["id"], "robert", "Whole item, latest revision")
    assert c2["rev"] == "B" and c2["anchor"] == {"type": "item"}
    with pytest.raises(T.Refused):
        store.add_comment(t, d["id"], "robert", "x", rev="Q")
    with pytest.raises(T.Refused):
        store.add_comment(t, d["id"], "robert", "x", reply_to="m-000000000000")
    with pytest.raises(T.Refused):
        store.add_comment(t, d["id"], "robert", "x" * 4001)


@pytest.mark.parametrize("anchor,kind", [({"type": "point", "x": 0.2, "y": 0.4, "w": 390}, "document"), ({"type": "block", "index": 1}, "design"),
                                         ({"type": "block", "index": -1}, "document"), ({"type": "block", "index": True}, "document"),
                                         ({"type": "point", "x": 2, "y": 0.4}, "design"), ({"type": "point", "x": "a", "y": 0}, "design"),
                                         ({"type": "weird"}, "note"), ("string", "note")])
def test_an_anchor_that_does_not_fit_the_item_is_refused(store, anchor, kind):
    t = _topic(store)["id"]
    kw = {"image": _img()} if kind == "design" else {"text": "t"}
    i = store.add_item(t, "robert", kind, "x", **kw)
    with pytest.raises(T.Refused):
        store.add_comment(t, i["id"], "robert", "c", anchor=anchor)


def test_a_design_comment_can_point_at_a_place_in_the_image(store):
    t = _topic(store)["id"]
    g = store.add_item(t, "robert", "design", "Files panel", image=_img())
    c = store.add_comment(t, g["id"], "robert", "This name is cut off", anchor={"type": "point", "x": 0.31, "y": 0.52, "w": 390})
    assert c["anchor"] == {"type": "point", "x": 0.31, "y": 0.52, "w": 390}


def test_resolve_and_disposition_are_events_with_who_and_which_revision(store):
    t = _topic(store)["id"]
    d = store.add_item(t, "robert", "document", "Spec", text="one")
    c = store.add_comment(t, d["id"], "robert", "Question")
    store.add_revision(t, d["id"], "robert", text="two", by="Claude Code")
    r = store.resolve_comment(t, d["id"], "robert", c["id"])
    assert r["status"] == "resolved" and r["resolved_by"] == "robert"
    assert store.resolve_comment(t, d["id"], "robert", c["id"], resolved=False)["status"] == "open"
    got = store.set_disposition(t, d["id"], "robert", c["id"], "accepted")
    assert got["disposition"]["value"] == "accepted" and got["disposition"]["rev"] == "B" and got["disposition"]["by"] == "robert"
    with pytest.raises(T.Refused):
        store.set_disposition(t, d["id"], "robert", c["id"], "approved-by-me")
    with pytest.raises(T.Refused):
        store.set_disposition(t, d["id"], "robert", "m-000000000000", "accepted")
    lines = [json.loads(x) for x in open(os.path.join(store.dir, t, "items", d["id"], "comments.jsonl"))]
    assert [x["t"] for x in lines] == ["comment", "resolve", "resolve", "disposition"]               # append-only: nothing was rewritten


def test_request_stage_is_set_by_hand_and_says_so(store):
    t = _topic(store)["id"]
    r = store.add_item(t, "robert", "request", "Review", text="please", request={"to": "Codex"})
    r = store.set_stage(t, r["id"], "robert", "delivered")
    assert r["request"]["stage"] == "delivered" and r["request"]["stage_source"] == "set by the owner"
    assert store.set_stage(t, r["id"], "robert", None, recipient="Grok CLI")["request"]["to"] == "Grok CLI"
    with pytest.raises(T.Refused):
        store.set_stage(t, r["id"], "robert", "approved")
    n = store.add_item(t, "robert", "note", "n", text="x")
    with pytest.raises(T.Refused):
        store.set_stage(t, n["id"], "robert", "delivered")


def test_layout_is_per_owner_and_only_names_real_items(store):
    t = _topic(store)["id"]
    a = store.add_item(t, "robert", "note", "a", text="x")["id"]
    b = store.add_item(t, "robert", "note", "b", text="y")["id"]
    assert store.set_layout(t, "robert", order=[b, a], wide=[a], last_view="split") == {"order": [b, a], "wide": [a], "last_view": "split"}
    assert store.get_layout(t, "robert")["order"] == [b, a]
    for bad in ([a, a], [a, "i-000000000000"], "nope"):
        with pytest.raises(T.Refused):
            store.set_layout(t, "robert", order=bad)
    with pytest.raises(T.Refused):
        store.set_layout(t, "robert", last_view="triple")


def test_the_journal_is_append_only_attributed_and_bounded(store, tmp_path):
    t = _topic(store)["id"]
    e = store.append_journal(t, "robert", author={"kind": "owner", "name": "Robert"}, via="ui", kind="decision", text="Go with F.",
                             refs=[{"type": "item", "ref": "i-0123456789ab", "quote": "Chat | Cards | Split"}])
    a = store.append_journal(t, "robert", author={"kind": "agent", "name": "Codex", "model": "gpt-5.5"}, via="inbox", kind="disagreement",
                             text="Ask MC belongs in the menu.", refs=[{"type": "file", "ref": "UI_AND_MORNING_REVIEW_codex.md"}])
    assert e["verified"] is False and a["verified"] is False and a["author"]["model"] == "gpt-5.5" and a["via"] == "inbox"
    assert [x["id"] for x in store.journal(t, "robert")] == [e["id"], a["id"]]
    for bad in ({"kind": "stranger", "name": "x"}, {"kind": "agent", "name": ""}, "Codex"):
        with pytest.raises(T.Refused):
            store.append_journal(t, "robert", author=bad, via="ui", kind="note", text="x")
    with pytest.raises(T.Refused):
        store.append_journal(t, "robert", author={"kind": "owner", "name": "R"}, via="telepathy", kind="note", text="x")
    with pytest.raises(T.Refused):
        store.append_journal(t, "robert", author={"kind": "owner", "name": "R"}, via="ui", kind="gossip", text="x")
    with pytest.raises(T.Refused):
        store.append_journal(t, "robert", author={"kind": "owner", "name": "R"}, via="ui", kind="note", text="x", refs=[{"type": "carrier-pigeon", "ref": "x"}])
    with pytest.raises(T.Refused):
        store.append_journal(t, "robert", author={"kind": "owner", "name": "R"}, via="ui", kind="note", text="x" * 8001)


def test_an_unreadable_file_is_skipped_and_counted_not_fatal(store, tmp_path):
    t = _topic(store)["id"]
    good = store.add_item(t, "robert", "note", "good", text="x")
    bad = store.add_item(t, "robert", "note", "bad", text="y")
    open(tmp_path / "topics" / t / "items" / bad["id"] / "item.json", "w").write("{not json")
    with open(tmp_path / "topics" / t / "items" / good["id"] / "comments.jsonl", "w") as f:
        f.write("garbage\n" + json.dumps({"t": "comment", "id": "m-0123456789ab", "item": good["id"], "rev": "A", "anchor": {"type": "item"}, "by": {"kind": "owner", "name": "robert"}, "text": "kept", "at": "2026-10-07T00:00:00+00:00", "reply_to": None}) + "\n")
    items = store.get_topic(t, "robert")["items"]
    assert [i["id"] for i in items] == [good["id"]] and store.unreadable >= 1
    assert [c["text"] for c in store.comments(t, good["id"], "robert")] == ["kept"]


def test_caps_are_enforced(store, monkeypatch):
    t = _topic(store)["id"]
    monkeypatch.setattr(T, "ITEMS_MAX", 2)
    store.add_item(t, "robert", "note", "1", text="x")
    store.add_item(t, "robert", "note", "2", text="x")
    with pytest.raises(T.Refused) as e:
        store.add_item(t, "robert", "note", "3", text="x")
    assert e.value.code == "too_many"
    with pytest.raises(T.Refused):
        store.add_item(t, "robert", "document", "big", text="x" * 200_001)
    with pytest.raises(T.Refused):
        store.add_item(t, "robert", "note", "bignote", text="x" * 8_001)


def test_no_guild_data_folder_means_unavailable_not_a_crash():
    s = T.TopicStore(None)
    with pytest.raises(T.TopicStoreUnavailable):
        s.create_topic("robert", "x")


def test_conversation_link_is_checked(store):
    t = _topic(store)["id"]
    assert store.set_conversation(t, "robert", "c-0123456789ab")["conversation_id"] == "c-0123456789ab"
    with pytest.raises(T.Refused):
        store.set_conversation(t, "robert", "../../etc/passwd")


def test_a_crash_that_leaves_half_a_line_does_not_swallow_the_next_entry(store):
    t = _topic(store)["id"]
    store.append_journal(t, "robert", author={"kind": "owner", "name": "R"}, via="ui", kind="note", text="first")
    path = os.path.join(store.dir, t, "journal.jsonl")
    with open(path, "ab") as f:
        f.write(b'{"id": "j-0123456789ab", "at": "2026-10-07T00:00:00+00:00", "text": "torn')      # power lost mid-write: no closing, no newline
    store.append_journal(t, "robert", author={"kind": "owner", "name": "R"}, via="ui", kind="note", text="second")
    assert [e["text"] for e in store.journal(t, "robert")] == ["first", "second"]                    # the torn line is skipped, the new one survives
    assert store.unreadable >= 1


def test_an_orphan_revision_file_from_a_crash_is_set_aside_and_the_letter_can_be_used(store, tmp_path):
    t = _topic(store)["id"]
    d = store.add_item(t, "robert", "document", "Spec", text="one")
    orphan = tmp_path / "topics" / t / "items" / d["id"] / "rev" / "B.md"
    orphan.write_text("written, then the power went before item.json named it")
    d = store.add_revision(t, d["id"], "robert", text="two")
    assert d["current_rev"] == "B" and store.get_item(t, d["id"], "robert")["text"] == "two"
    kept = [p.name for p in orphan.parent.iterdir() if p.name.startswith("B.md.orphan-")]
    assert len(kept) == 1 and (orphan.parent / kept[0]).read_text().startswith("written, then")      # kept, never silently deleted or reused


def test_verify_reports_what_a_crash_can_leave_and_changes_nothing(store, tmp_path):
    t = _topic(store)["id"]
    d = store.add_item(t, "robert", "document", "Spec", text="one")
    clean = store.verify("robert")
    assert clean["ok"] is True and clean["topics"] == 1 and clean["items"] == 1
    base = tmp_path / "topics" / t / "items" / d["id"]
    (base / "rev" / "Z.md").write_text("orphan")
    (tmp_path / "topics" / t / "items" / "i-aaaaaaaaaaaa").mkdir()
    with open(base / "comments.jsonl", "w") as f:
        f.write("not json\n")
    os.unlink(base / "rev" / "A.md")
    r = store.verify("robert")
    assert r["ok"] is False
    assert f"{t}/{d['id']}/Z.md" in r["orphan_revision_files"] and f"{t}/i-aaaaaaaaaaaa" in r["items_without_record"]
    assert f"{t}/{d['id']}/A.md" in r["missing_revision_files"] and f"{t}/{d['id']}/comments.jsonl" in r["torn_or_malformed_lines"]
    assert (base / "rev" / "Z.md").exists()                                                             # a report, never a repair


def test_a_plain_copy_of_the_folder_restores_everything(store, tmp_path):
    """Backup and restore, synthetic: copy the topics folder as a backup tool would, open a store on the copy, and read it all back."""
    import shutil
    t = _topic(store)["id"]
    n = store.add_item(t, "robert", "note", "A note", text="keep this")
    d = store.add_item(t, "robert", "document", "Spec", text="one")
    store.add_revision(t, d["id"], "robert", text="two", by="Claude Code")
    g = store.add_item(t, "robert", "design", "Files panel", image=_img())
    r = store.add_item(t, "robert", "request", "Review", text="please", request={"to": "Codex"})
    store.set_stage(t, r["id"], "robert", "delivered")
    c = store.add_comment(t, d["id"], "robert", "A question", rev="A", anchor={"type": "block", "index": 0, "quote": "one"})
    store.set_disposition(t, d["id"], "robert", c["id"], "accepted")
    store.set_archived(t, n["id"], "robert", True)
    store.set_layout(t, "robert", order=[g["id"], d["id"]], last_view="split")
    store.append_journal(t, "robert", author={"kind": "agent", "name": "Codex"}, via="inbox", kind="review", text="Looks right.")
    shutil.copytree(tmp_path / "topics", tmp_path / "restored" / "topics")
    back = T.TopicStore(str(tmp_path / "restored"))
    assert back.verify("robert")["ok"] is True
    a, b = store.get_topic(t, "robert"), back.get_topic(t, "robert")
    assert a == b                                                                                         # topic, items, comment counts, layout: identical
    assert back.get_item(t, d["id"], "robert", rev="A")["text"] == "one" and back.get_item(t, d["id"], "robert")["text"] == "two"
    assert back.comments(t, d["id"], "robert") == store.comments(t, d["id"], "robert") and back.comments(t, d["id"], "robert")[0]["disposition"]["value"] == "accepted"
    assert [e["text"] for e in back.journal(t, "robert")] == ["Looks right."]
    path, mime = back.design_file(t, g["id"], "robert", None)
    assert open(path, "rb").read() == b"\x89PNG-sample" and mime == "image/png"
    assert back.get_item(t, n["id"], "robert")["archived"] is True and back.get_layout(t, "robert")["last_view"] == "split"
