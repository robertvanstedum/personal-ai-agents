"""Master Craftsman reads files you attach to a message (Guild 1.1, upload reading).

Real portal, real OpenClaw adapter, a scripted relay that RECORDS what is sent. Every claim the page makes about which
files reached the model is checked against that recording. Synthetic content only; no model, no network."""
from __future__ import annotations

import io
import json
import re
import stat
import uuid
from pathlib import Path

import pytest

from doc_fixtures import INJECTION, docx_bytes, pdf_bytes
from floor_helpers import load_portal, write_headers  # noqa: F401  (load_portal is a pytest fixture)
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401  (pytest fixtures)
from board_media_helpers import image_bytes
from test_mc_streaming import (StreamRuntime, _events, _fresh_dispatch_state, _stream, _wait_idle,  # noqa: F401
                               streaming)
from test_mc_turn_capture import mc_dir, mc_lines  # noqa: F401
from test_mc_turns import API, FakeRuntime, _keep, _openclaw, _turn_on, turned  # noqa: F401

CARD = "5555 5555 5555 4444"


# ── helpers ──────────────────────────────────────────────────────────────────────

def _new(client, token):
    r = client.post(f"{API}/conversations", json=keyed(), headers=write_headers(token))
    assert r.status_code == 200, r.get_json()
    return r.get_json()["conversation"]["id"]


def _upload(client, token, cid, raw, name, mode="on_record", csrf=True):
    headers = write_headers(token, mode=mode) if csrf else {"X-Record-Mode": mode}
    headers["Idempotency-Key"] = uuid.uuid4().hex
    return client.post(f"{API}/conversations/{cid}/documents", data={"file": (io.BytesIO(raw), name)},
                       headers=headers, content_type="multipart/form-data")


def _the(client, cid):
    """This conversation, picked by its id (the list is ordered by time, and ties are not an order)."""
    return next(c for c in client.get(f"{API}/conversations?view=active").get_json()["conversations"] if c["id"] == cid)


def _doc(client, token, cid, text, name="notes.txt"):
    r = _upload(client, token, cid, text.encode() if isinstance(text, str) else text, name)
    assert r.status_code == 200, r.get_json()
    return r.get_json()["document"]["id"]


def _ask(client, token, note_id, cid, ids=None, mode="on_record", **extra):
    body = {"note_request_id": note_id, "record_mode": mode, "conversation_id": cid, "request_id": uuid.uuid4().hex, **extra}
    if ids is not None:
        body["attachment_ids"] = ids
    return client.post(f"{API}/mc/turns", json=body, headers=write_headers(token, mode=mode))


def _note(client, token, cid, text="Please summarise the attached file."):
    return _keep(client, token, text, conversation_id=cid)


def _sent(portal):
    """The one message that actually left for Master Craftsman."""
    [post] = portal.extra["runtime"].sent
    [message] = post["body"]["messages"]
    return message["content"]


def _files_in(content):
    """Parse the data blocks out of what was sent: [(number, name, nonce, text)]."""
    out = []
    for m in re.finditer(r'=====BEGIN FILE (\d+) · name: "([^"]*)" · type: [^·]+ · characters shown: [\d,]+ of [\d,]+ · '
                         r"boundary: ([0-9a-f]{12})=====\n(.*?)\n=====END FILE \1 · boundary: \3=====", content, re.S):
        out.append((int(m.group(1)), m.group(2), m.group(3), m.group(4)))
    return out


# ── keeping a document ───────────────────────────────────────────────────────────

def test_an_uploaded_document_keeps_its_text_and_its_original_in_private_files(turned):
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    r = _upload(client, token, cid, b"Budget: 4200 EUR\nOwner: Ana", "budget.txt")
    body = r.get_json()
    assert r.status_code == 200 and body["result"] == "added" and body["message"] == "Read 27 characters · original kept"
    [doc] = body["conversation"]["documents"]
    assert doc["name"] == "budget.txt" and doc["kind"] == "text" and doc["chars"] == 27 and "sha256" not in doc
    store = turned.app.extensions["guild_ui_next"]["services"]
    from minimoi_portal.guild_ui.conversations import conversations_of
    convs = conversations_of(store)
    path = Path(convs.dir) / "docs" / cid / f"{doc['id']}.txt"
    assert path.read_text() == "Budget: 4200 EUR\nOwner: Ana"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    orig = path.with_suffix(".orig")                                          # the original is kept beside it (step 2), privately
    assert orig.read_bytes() == b"Budget: 4200 EUR\nOwner: Ana" and stat.S_IMODE(orig.stat().st_mode) == 0o600
    assert not list(Path(convs.dir).glob("**/budget.txt"))                    # never under its own name
    assert b"Budget" not in (Path(convs.dir) / f"{cid}.json").read_bytes()    # the conversation file holds metadata only


def test_the_same_file_twice_is_one_document(turned):
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    first = _doc(client, token, cid, "same text", "a.txt")
    again = _upload(client, token, cid, b"same text", "a.txt").get_json()
    assert again["result"] == "already" and again["document"]["id"] == first and len(again["conversation"]["documents"]) == 1


@pytest.mark.parametrize("raw,name,status,code", [
    (image_bytes("PNG"), "shot.png", 415, "image"),
    (b"PK", "sheet.xlsx", 415, "unsupported"),
    (b"abc\x00\x01", "blob.txt", 422, "binary"),
    (pdf_bytes(["", ""]), "scan.pdf", 422, "no_text"),
    (b"a" * (5 * 1024 * 1024 + 1), "huge.txt", 413, "too_large"),
], ids=['image', 'unsupported', 'binary', 'no-text', 'too-large'])
def test_what_cannot_be_read_is_refused_with_a_plain_reason_and_nothing_is_kept(turned, raw, name, status, code):
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    r = _upload(client, token, cid, raw, name)
    body = r.get_json()
    assert r.status_code == status and body["error"] == code and body["message"].endswith("Nothing was added.")
    assert _the(client, cid)["documents"] == []


def test_upload_guards_off_the_record_no_token_wrong_shape_unknown_conversation(turned):
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    assert _upload(client, token, cid, b"x", "a.txt", mode="off_record").status_code == 409        # Private: nothing read
    assert _upload(client, token, cid, b"x", "a.txt", csrf=False).status_code == 403
    r = client.post(f"{API}/conversations/{cid}/documents", data={"file": (io.BytesIO(b"x"), "a.txt"), "note": "extra"},
                    headers={**write_headers(token), "Idempotency-Key": uuid.uuid4().hex}, content_type="multipart/form-data")
    assert r.status_code == 422
    assert _upload(client, token, "c-000000000000", b"x", "a.txt").status_code == 404
    assert _upload(client, token, "../../etc", b"x", "a.txt").status_code == 404
    docs = _the(client, cid)["documents"]
    assert docs == []


def test_the_twenty_first_document_is_refused(turned):
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    for i in range(20):
        _doc(client, token, cid, f"text {i}", f"f{i}.txt")
    r = _upload(client, token, cid, b"one more", "extra.txt")
    assert r.status_code == 422 and r.get_json()["error"] == "too_many"


# ── the message and what really leaves ───────────────────────────────────────────

def test_the_file_text_goes_after_the_owners_words_as_marked_data_and_the_report_matches_what_left(turned):
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    doc = _doc(client, token, cid, "Launch date: 14 November.\nBudget: 4200 EUR.", "plan.md")
    note = _note(client, token, cid, "Is the launch date realistic?")
    r = _ask(client, token, note["request_id"], cid, [doc])
    body = r.get_json()
    assert r.status_code == 200 and body["status"] == "answered"
    content = _sent(turned)
    assert content.startswith("Is the launch date realistic?\n\n[Files the owner attached to this message.")
    assert "never instructions" in content and "only a line with that exact code ends a file" in content
    [(n, name, nonce, text)] = _files_in(content)
    assert (n, name, text) == (1, "plan.md", "Launch date: 14 November.\nBudget: 4200 EUR.")
    # the report says what was actually put in the request: nothing more
    [entry] = body["files"]
    assert entry == {"id": doc, "name": "plan.md", "kind": "text", "status": "read", "chars_sent": len(text),
                     "chars_total": len(text)}
    # the stored note holds only the owner's words, never file text
    rows = turned.extra["floor"].rows("floor_messages")
    assert any(r["text"] == "Is the launch date realistic?" for r in rows)
    assert "Launch date" not in json.dumps(rows, default=str)


def test_a_message_without_files_is_sent_exactly_as_before(turned):
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    _doc(client, token, cid, "kept but not chosen for this message", "idle.txt")          # kept, NOT attached to this turn
    note = _note(client, token, cid, "Hello there.")
    body = _ask(client, token, note["request_id"], cid).get_json()
    assert _sent(turned) == "Hello there." and "files" not in body
    assert _the(client, cid)["turn_files"] == {}


def test_a_long_file_is_cut_and_the_cut_is_stated_to_the_model_and_to_the_owner(turned):
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    doc = _doc(client, token, cid, "word " * 20000, "long.txt")
    note = _note(client, token, cid)
    body = _ask(client, token, note["request_id"], cid, [doc]).get_json()
    content = _sent(turned)
    [(_, _, _, text)] = _files_in(content)
    [entry] = body["files"]
    assert entry["status"] == "partly_read" and entry["chars_sent"] == len(text) == 30000
    assert entry["reason"] == "only the first 30,000 of 100,000 characters were sent"
    assert "characters shown: 30,000 of 100,000" in content
    # the cut is stated outside the file's own lines
    assert content.rstrip().endswith("[File 1 was cut: only the first 30,000 of 100,000 characters were sent.]")
    assert "[File 1 was cut" not in text


def test_the_message_budget_is_shared_and_a_file_that_gets_no_room_is_not_read(turned):
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    docs = [_doc(client, token, cid, f"{i}" * 30000, f"part{i}.txt") for i in (1, 2, 3)]
    body = _ask(client, token, _note(client, token, cid)["request_id"], cid, docs).get_json()
    sent_texts = _files_in(_sent(turned))
    statuses = [e["status"] for e in body["files"]]
    assert statuses == ["read", "read", "not_read"]
    assert [e["chars_sent"] for e in body["files"]] == [30000, 30000, 0] == [len(t[3]) for t in sent_texts] + [0]
    assert body["files"][2]["reason"] == "this message already carries the most text it can"
    assert 'Attached but NOT shown to you: "part3.txt" (no room left in this message)' in _sent(turned)


def test_an_image_is_reported_not_read_and_the_model_is_told_it_was_not_shown(turned, tmp_path):
    folder = tmp_path / "media"
    folder.mkdir()
    turned.app.extensions["guild_ui_next"]["services"].media_dir = str(folder)
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    up = client.post(f"{API}/media", data={"file": (io.BytesIO(image_bytes("PNG")), "board.png")},
                     headers={**write_headers(token), "Idempotency-Key": uuid.uuid4().hex}, content_type="multipart/form-data")
    asset = up.get_json()["asset"]["id"]
    assert client.post(f"{API}/conversations/{cid}/attachments", json=keyed(asset_id=asset, name="board.png"),
                       headers=write_headers(token)).status_code == 200
    doc = _doc(client, token, cid, "Some words.", "words.txt")
    body = _ask(client, token, _note(client, token, cid)["request_id"], cid, [asset, doc]).get_json()
    assert [(e["name"], e["status"]) for e in body["files"]] == [("board.png", "not_read"), ("words.txt", "read")]
    assert body["files"][0]["reason"] == "Master Craftsman can't see images yet" and body["files"][0]["chars_sent"] == 0
    content = _sent(turned)
    assert 'Attached but NOT shown to you: "board.png" (an image; you cannot see images)' in content
    assert _files_in(content)[0][3] == "Some words." and len(_files_in(content)) == 1
    assert "PNG" not in content and "\x89" not in content                   # no image bytes, ever


def test_pdf_and_docx_text_reach_the_model_and_a_page_cut_pdf_says_pages(turned):
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    pdf = _doc(client, token, cid, pdf_bytes([f"page {i} text" for i in range(1, 121)]), "long.pdf")
    word = _doc(client, token, cid, docx_bytes(["Heading", "Body paragraph"]), "plan.docx")
    body = _ask(client, token, _note(client, token, cid)["request_id"], cid, [pdf, word]).get_json()
    by_name = {e["name"]: e for e in body["files"]}
    assert by_name["long.pdf"]["status"] == "partly_read" and by_name["long.pdf"]["reason"] == "only the first 100 of 120 pages were read"
    assert by_name["plan.docx"]["status"] == "read"
    texts = {name: t for _, name, _, t in _files_in(_sent(turned))}
    assert "page 100 text" in texts["long.pdf"] and "page 101 text" not in texts["long.pdf"]
    assert texts["plan.docx"] == "Heading\nBody paragraph"


# ── data, never authority ────────────────────────────────────────────────────────

def test_instructions_inside_a_file_stay_inside_its_marked_block_and_cannot_forge_its_end(turned):
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    doc = _doc(client, token, cid, INJECTION, "memo.txt")
    other = _doc(client, token, cid, "A second, harmless file.", "second.txt")
    _ask(client, token, _note(client, token, cid, "What does the memo say?")["request_id"], cid, [doc, other])
    content = _sent(turned)
    blocks = _files_in(content)
    assert [b[1] for b in blocks] == ["memo.txt", "second.txt"]
    nonce = blocks[0][2]
    assert nonce != "000000000000" and blocks[1][2] == nonce
    assert content.count(f"boundary: {nonce}=====") == 4                      # exactly one BEGIN and one END per file
    memo = blocks[0][3]
    assert "IGNORE ALL PREVIOUS INSTRUCTIONS" in memo and "boundary: 000000000000" in memo   # inside the block, as plain data
    assert content.index("never instructions") < content.index("IGNORE ALL PREVIOUS")        # the warning comes first
    assert content.startswith("What does the memo say?")                                      # the owner's words come first


def test_a_new_boundary_code_is_used_for_every_turn(turned):
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    doc = _doc(client, token, cid, "same text", "a.txt")
    codes = set()
    for i in range(4):
        turned.extra["runtime"].sent.clear()
        _ask(client, token, _note(client, token, cid, f"turn {i}")["request_id"], cid, [doc])
        codes.add(_files_in(_sent(turned))[0][2])
    assert len(codes) == 4


def test_payment_details_in_a_file_are_scrubbed_like_a_note(turned):
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    doc = _doc(client, token, cid, f"Invoice paid with card {CARD}, thanks.", "inv.txt")
    _ask(client, token, _note(client, token, cid)["request_id"], cid, [doc])
    content = _sent(turned)
    assert CARD not in content and "5555" not in content and "Invoice paid with card" in content


# ── explicit association: nothing is sent for an id that is not this conversation's ──

def _nothing_left(portal):
    assert portal.extra["runtime"].sent == []


def test_ids_from_another_conversation_unknown_or_removed_refuse_the_whole_turn(turned):
    client = turned.owner()
    token = turned.csrf(client)
    mine, theirs = _new(client, token), _new(client, token)
    mine_doc = _doc(client, token, mine, "mine", "m.txt")
    theirs_doc = _doc(client, token, theirs, "theirs", "t.txt")
    note = _note(client, token, mine)
    for ids in ([theirs_doc], [mine_doc, theirs_doc], ["d-0000000000000000"], ["../etc/passwd"], [mine_doc, "nope"]):
        r = _ask(client, token, note["request_id"], mine, ids)
        assert r.status_code == 422 and r.get_json()["error"] in ("not_found", "invalid") or r.status_code == 422, ids
        assert r.get_json()["message"].endswith("Nothing was sent.")
    _nothing_left(turned)
    removed = client.post(f"{API}/conversations/{mine}/documents/remove", json=keyed(document_id=mine_doc),
                          headers=write_headers(token)).get_json()
    assert removed["result"] == "removed"
    assert _ask(client, token, note["request_id"], mine, [mine_doc]).status_code == 422
    _nothing_left(turned)
    assert _the(client, mine)["turn_files"] == {}


@pytest.mark.parametrize("ids", ["d-0123456789abcdef", {"a": 1}, [1, 2], ["a"] * 11 + ["b"]])
def test_a_malformed_id_list_is_refused_before_anything_else(turned, ids):
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    note = _note(client, token, cid)
    r = _ask(client, token, note["request_id"], cid, ids)
    assert r.status_code == 422 and r.get_json()["message"].endswith("Nothing was sent.")
    _nothing_left(turned)


def test_more_than_five_documents_in_one_message_is_refused(turned):
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    docs = [_doc(client, token, cid, f"text {i}", f"f{i}.txt") for i in range(6)]
    r = _ask(client, token, _note(client, token, cid)["request_id"], cid, docs)
    assert r.status_code == 422 and r.get_json()["error"] == "too_many"
    _nothing_left(turned)


# ── Private ──────────────────────────────────────────────────────────────────────

def test_a_private_turn_with_files_sends_nothing_and_records_nothing(turned):
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    doc = _doc(client, token, cid, "secret plan", "p.txt")
    note = _note(client, token, cid)
    r = _ask(client, token, note["request_id"], cid, [doc], mode="off_record")
    assert r.status_code == 409
    _nothing_left(turned)
    assert _the(client, cid)["turn_files"] == {}


# ── what the page can say afterwards ─────────────────────────────────────────────

def test_the_report_is_saved_with_the_conversation_without_any_file_text(turned):
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    doc = _doc(client, token, cid, "Confidential launch numbers: 777", "numbers.txt")
    note = _note(client, token, cid)
    body = _ask(client, token, note["request_id"], cid, [doc]).get_json()
    conv = _the(client, cid)
    assert conv["turn_files"] == {note["request_id"]: body["files"]}
    assert "777" not in json.dumps(conv) and "Confidential" not in json.dumps(conv)


def test_a_turn_that_got_no_answer_still_reports_the_files_that_were_in_the_request(turned):
    from test_mc_turns import Resp
    turned.extra["runtime"].answer = lambda b, h: Resp(500, '{"error":{"message":"No connected db."}}')
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    doc = _doc(client, token, cid, "words", "w.txt")
    note = _note(client, token, cid)
    body = _ask(client, token, note["request_id"], cid, [doc]).get_json()
    assert body["status"] == "unavailable" and body["reply_note"] is None
    assert body["files"][0]["status"] == "read"             # it was in the request; the page words this as "no answer"
    assert len(_files_in(_sent(turned))) == 1


def test_only_the_newest_hundred_reports_are_kept(turned):
    from minimoi_portal.guild_ui.conversations import conversations_of
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    convs = conversations_of(turned.app.extensions["guild_ui_next"]["services"])
    for i in range(105):
        convs.record_turn_files(cid, "robert", f"note-{i:04d}-xxxx", [{"name": f"f{i}", "status": "read"}])
    kept = convs.get(cid, "robert")["turn_files"]
    assert len(kept) == 100 and "note-0104-xxxx" in kept and "note-0004-xxxx" not in kept and "note-0005-xxxx" in kept


# ── the capture and the memory path never get file text ──────────────────────────

def test_the_turn_capture_keeps_the_owners_words_only(turned, mc_dir):
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    doc = _doc(client, token, cid, "Confidential launch numbers: 777", "numbers.txt")
    note = _note(client, token, cid, "Summarise it.")
    assert _ask(client, token, note["request_id"], cid, [doc]).get_json()["history_saved"] is True
    [rec] = mc_lines(mc_dir)
    assert rec["user_text"] == "Summarise it." and "777" not in json.dumps(rec) and "BEGIN FILE" not in json.dumps(rec)


# ── removal ──────────────────────────────────────────────────────────────────────

def test_remove_deletes_the_saved_text_and_is_safe_to_repeat(turned):
    from minimoi_portal.guild_ui.conversations import conversations_of
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    doc = _doc(client, token, cid, "to be removed", "gone.txt")
    convs = conversations_of(turned.app.extensions["guild_ui_next"]["services"])
    path = Path(convs.dir) / "docs" / cid / f"{doc}.txt"
    assert path.exists()
    first = client.post(f"{API}/conversations/{cid}/documents/remove", json=keyed(document_id=doc), headers=write_headers(token))
    assert first.get_json()["result"] == "removed" and first.get_json()["conversation"]["documents"] == [] and not path.exists()
    again = client.post(f"{API}/conversations/{cid}/documents/remove", json=keyed(document_id=doc), headers=write_headers(token))
    assert again.status_code == 200 and again.get_json()["result"] == "nothing_to_remove"
    bad = client.post(f"{API}/conversations/{cid}/documents/remove", json=keyed(document_id="../../x"), headers=write_headers(token))
    assert bad.status_code == 422
    assert client.post(f"{API}/conversations/{cid}/documents/remove", json=keyed(document_id=doc),
                       headers=write_headers(token, mode="off_record")).status_code == 409


def test_a_failed_delete_leaves_the_document_listed_so_remove_can_be_retried(turned, monkeypatch):
    from minimoi_portal.guild_ui import conversations as cv
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    doc = _doc(client, token, cid, "stays until deleted", "keep.txt")
    real = cv.os.unlink

    def deny(path, *a, **kw):
        if str(path).endswith(f"{doc}.txt"):
            raise PermissionError("denied")
        return real(path, *a, **kw)
    monkeypatch.setattr(cv.os, "unlink", deny)
    r = client.post(f"{API}/conversations/{cid}/documents/remove", json=keyed(document_id=doc), headers=write_headers(token))
    assert r.status_code == 503
    assert [d["id"] for d in _the(client, cid)["documents"]] == [doc]
    monkeypatch.setattr(cv.os, "unlink", real)
    assert client.post(f"{API}/conversations/{cid}/documents/remove", json=keyed(document_id=doc),
                       headers=write_headers(token)).get_json()["result"] == "removed"


def test_a_document_whose_text_file_vanished_is_reported_not_read_and_the_rest_is_still_sent(turned):
    from minimoi_portal.guild_ui.conversations import conversations_of
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    lost = _doc(client, token, cid, "this will vanish", "lost.txt")
    kept = _doc(client, token, cid, "this stays", "kept.txt")
    convs = conversations_of(turned.app.extensions["guild_ui_next"]["services"])
    (Path(convs.dir) / "docs" / cid / f"{lost}.txt").unlink()
    body = _ask(client, token, _note(client, token, cid)["request_id"], cid, [lost, kept]).get_json()
    assert [(e["name"], e["status"]) for e in body["files"]] == [("lost.txt", "not_read"), ("kept.txt", "read")]
    assert body["files"][0]["reason"] == "its saved text is missing"
    assert [b[1] for b in _files_in(_sent(turned))] == ["kept.txt"]


# ── streaming: the same files, the same report, in ack ───────────────────────────

def test_a_streamed_turn_carries_the_report_in_ack_and_sends_the_same_block(streaming):
    client = streaming.owner()
    token = streaming.csrf(client)
    cid = _new(client, token)
    doc = _doc(client, token, cid, "Streamed file text.", "s.txt")
    note = _note(client, token, cid, "Read it.")
    res = _stream(client, token, note["request_id"], conversation_id=cid, attachment_ids=[doc])
    events = _events(res)
    ack = events[0]
    assert ack["t"] == "ack" and [(e["name"], e["status"], e["chars_sent"]) for e in ack["files"]] == [("s.txt", "read", 19)]
    _wait_idle()
    [post] = streaming.extra["runtime"].posts
    content = post["body"]["messages"][0]["content"]
    assert post["body"]["stream"] is True and content.startswith("Read it.\n\n[Files the owner attached")
    assert _files_in(content)[0][3] == "Streamed file text."
    saved = _the(client, cid)["turn_files"]
    assert saved == {note["request_id"]: ack["files"]}


def test_a_streamed_turn_abandoned_before_it_starts_records_no_files(streaming):
    from minimoi_portal.guild_ui.api import open_mc_stream
    from minimoi_portal.guild_ui.conversations import conversations_of
    client = streaming.owner()
    token = streaming.csrf(client)
    cid = _new(client, token)
    doc = _doc(client, token, cid, "never sent", "n.txt")
    note_id = _note(client, token, cid)["request_id"]
    services = streaming.extra["services"]
    convs = conversations_of(services)
    conv = convs.get(cid, "robert")
    base, nf = services.floor, conv.get("notes_floor")
    floor = base if not nf or nf == base.floor else base.for_floor(nf)
    opened = open_mc_stream(services, conversations=convs, floor=floor, conv=conv, principal="robert",
                            note=floor.get_note(note_id), note_id=note_id, request_id="req-" + "b" * 20,
                            attachment_ids=[doc])
    assert opened[0] == "stream"
    opened[1].abandon_if_not_started()                  # the browser left before ack: nothing was dispatched
    assert streaming.extra["runtime"].posts == []
    assert not convs.get(cid, "robert").get("turn_files")
