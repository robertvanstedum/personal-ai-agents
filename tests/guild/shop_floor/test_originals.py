"""Guild 1.1: the original of an uploaded file is kept privately, can be downloaded by its owner, and is deleted with
the document (overnight build, step 2; B3 of the amendment). Synthetic content only; no model, no network."""
from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

import pytest

from doc_fixtures import docx_bytes, pdf_bytes
from floor_helpers import load_portal, write_headers  # noqa: F401  (load_portal is a pytest fixture)
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401  (pytest fixtures)
from test_mc_turns import API, FakeRuntime, _keep, _openclaw, _turn_on, turned  # noqa: F401
from test_mc_upload_reading import _doc, _new, _the, _upload


def _convs(portal):
    from minimoi_portal.guild_ui.conversations import conversations_of
    return conversations_of(portal.app.extensions["guild_ui_next"]["services"])


def _orig_path(portal, cid, doc):
    return Path(_convs(portal).dir) / "docs" / cid / f"{doc}.orig"


def _download(client, cid, doc):
    return client.get(f"{API}/conversations/{cid}/documents/{doc}/original")


def test_a_docx_comes_back_byte_for_byte_with_safe_headers(turned):
    raw = docx_bytes(["Résumé: Ana Example", "Role: Analyst"])
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    r = _upload(client, token, cid, raw, "résumé.docx")
    assert r.status_code == 200
    doc = r.get_json()["document"]
    assert doc["has_original"] is True and "original kept" in r.get_json()["message"]
    got = _download(client, cid, doc["id"])
    assert got.status_code == 200 and got.data == raw and hashlib.sha256(got.data).hexdigest() == hashlib.sha256(raw).hexdigest()
    assert got.headers["Content-Disposition"].startswith("attachment;") and "filename*=UTF-8''r%C3%A9sum%C3%A9.docx" in got.headers["Content-Disposition"]
    assert got.headers["Content-Type"].startswith("application/octet-stream") and got.headers["X-Content-Type-Options"] == "nosniff"
    assert "no-store" in got.headers["Cache-Control"] and "sandbox" in got.headers["Content-Security-Policy"]


def test_a_pdf_original_is_kept_and_the_file_is_private(turned):
    raw = pdf_bytes(["page one", "page two"])
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    doc = _upload(client, token, cid, raw, "two.pdf").get_json()["document"]["id"]
    path = _orig_path(turned, cid, doc)
    assert path.read_bytes() == raw and stat.S_IMODE(path.stat().st_mode) == 0o600
    assert b"page one" not in (Path(_convs(turned).dir) / f"{cid}.json").read_bytes()
    pub = _the(client, cid)["documents"][0]
    assert pub["has_original"] is True and pub["original_bytes"] == len(raw) and "original_sha256" not in pub


def test_html_and_svg_originals_are_never_served_as_a_page(turned):
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    for name, raw in (("evil.md", b"<script>alert(1)</script>\n# Title"), ("evil.txt", b"<svg onload=alert(1)></svg>")):
        doc = _doc(client, token, cid, raw, name)
        got = _download(client, cid, doc)
        assert got.status_code == 200 and got.data == raw
        assert got.headers["Content-Type"].startswith("application/octet-stream") and "attachment" in got.headers["Content-Disposition"]
        assert "sandbox" in got.headers["Content-Security-Policy"]


def test_a_hostile_file_name_cannot_break_the_header(turned):
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    doc = _doc(client, token, cid, "x", 'a"; filename="b; X-Evil 1.txt')
    got = _download(client, cid, doc)
    assert got.status_code == 200 and "X-Evil" not in got.headers and "\r" not in got.headers["Content-Disposition"]
    assert got.headers["Content-Disposition"].count('filename="') == 1


def test_only_the_owner_can_download_and_only_from_the_conversation_it_belongs_to(turned):
    client = turned.owner()
    token = turned.csrf(client)
    a, b = _new(client, token), _new(client, token)
    doc = _doc(client, token, a, "private words", "mine.txt")
    assert _download(client, a, doc).status_code == 200
    assert _download(client, b, doc).status_code == 404                       # the id is resolved inside one conversation
    assert _download(client, "c-000000000000", doc).status_code == 404
    assert turned.client().get(f"{API}/conversations/{a}/documents/{doc}/original").status_code in (302, 401, 403)
    assert turned.guest().get(f"{API}/conversations/{a}/documents/{doc}/original").status_code in (302, 401, 403)


@pytest.mark.parametrize("bad", ["d-0000000000000000", "d-ZZZZZZZZZZZZZZZZ", "..%2f..%2fx", "x" * 300])
def test_an_id_that_is_not_one_of_the_owners_is_refused_without_touching_the_disk(turned, bad):
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    assert _download(client, cid, bad).status_code == 404


def test_a_symlink_put_where_the_original_should_be_is_refused(turned, tmp_path):
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    doc = _doc(client, token, cid, "real contents", "real.txt")
    secret = tmp_path / "secret.txt"
    secret.write_text("NOT FOR THE OWNER'S DOWNLOAD")
    path = _orig_path(turned, cid, doc)
    path.unlink()
    os.symlink(secret, path)
    got = _download(client, cid, doc)
    assert got.status_code == 404 and b"NOT FOR" not in got.data


def test_an_original_that_no_longer_matches_its_hash_is_not_served(turned):
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    doc = _doc(client, token, cid, "authentic", "a.txt")
    _orig_path(turned, cid, doc).write_bytes(b"tampered")
    assert _download(client, cid, doc).status_code == 404


def test_a_missing_original_says_so_and_the_text_still_works(turned):
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    doc = _doc(client, token, cid, "text stays", "t.txt")
    _orig_path(turned, cid, doc).unlink()
    got = _download(client, cid, doc)
    assert got.status_code == 404 and "No original was kept" in got.get_json()["message"]
    assert (Path(_convs(turned).dir) / "docs" / cid / f"{doc}.txt").exists()


def test_remove_deletes_the_original_with_the_text_and_says_copies_are_not_deleted(turned):
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    doc = _doc(client, token, cid, "bye", "bye.txt")
    orig, text = _orig_path(turned, cid, doc), Path(_convs(turned).dir) / "docs" / cid / f"{doc}.txt"
    assert orig.exists() and text.exists()
    r = client.post(f"{API}/conversations/{cid}/documents/remove", json=keyed(document_id=doc), headers=write_headers(token))
    body = r.get_json()
    assert body["result"] == "removed" and not orig.exists() and not text.exists()
    assert "original were deleted" in body["message"] and "a copy already made elsewhere" in body["message"]
    assert _download(client, cid, doc).status_code == 404


def test_a_failed_original_delete_keeps_the_document_listed_so_remove_can_be_retried(turned, monkeypatch):
    from minimoi_portal.guild_ui import conversations as cv
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    doc = _doc(client, token, cid, "stays", "stay.txt")
    real = cv.os.unlink

    def deny(path, *a, **kw):
        if str(path).endswith(f"{doc}.orig"):
            raise PermissionError("denied")
        return real(path, *a, **kw)
    monkeypatch.setattr(cv.os, "unlink", deny)
    assert client.post(f"{API}/conversations/{cid}/documents/remove", json=keyed(document_id=doc), headers=write_headers(token)).status_code == 503
    assert [d["id"] for d in _the(client, cid)["documents"]] == [doc]
    monkeypatch.setattr(cv.os, "unlink", real)
    assert client.post(f"{API}/conversations/{cid}/documents/remove", json=keyed(document_id=doc),
                       headers=write_headers(token)).get_json()["result"] == "removed"
    assert not _orig_path(turned, cid, doc).exists()


def test_a_failed_save_leaves_no_half_kept_files(turned, monkeypatch):
    from minimoi_portal.guild_ui import conversations as cv
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    real = cv.os.open

    def fail_orig(path, *a, **kw):
        if str(path).endswith(".orig"):
            raise OSError("disk full")
        return real(path, *a, **kw)
    monkeypatch.setattr(cv.os, "open", fail_orig)
    r = _upload(client, token, cid, b"will not be kept", "half.txt")
    assert r.status_code >= 500
    monkeypatch.setattr(cv.os, "open", real)
    folder = Path(_convs(turned).dir) / "docs" / cid
    assert not folder.exists() or list(folder.iterdir()) == []
    assert _the(client, cid)["documents"] == []


def test_past_the_storage_quota_the_text_is_kept_but_the_original_is_not_and_the_owner_is_told(turned, monkeypatch):
    from minimoi_portal.guild_ui import conversations as cv
    monkeypatch.setattr(cv, "ORIGINALS_TOTAL_MAX", 10)
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    r = _upload(client, token, cid, b"more than ten bytes", "big.txt")
    body = r.get_json()
    doc_id = body["document"]["id"]
    assert r.status_code == 200 and body["document"]["has_original"] is not True and body["document"]["original_skipped"] == "quota"
    assert "original not kept (storage full)" in body["message"]
    assert not _orig_path(turned, cid, doc_id).exists()
    assert (Path(_convs(turned).dir) / "docs" / cid / f"{doc_id}.txt").exists()
    assert _download(client, cid, doc_id).status_code == 404


def test_the_same_file_twice_is_still_one_document_with_one_original(turned):
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    raw = b"same bytes twice"
    _upload(client, token, cid, raw, "dup.txt")
    second = _upload(client, token, cid, raw, "dup.txt").get_json()
    assert len(second["conversation"]["documents"]) == 1
    assert len(list((Path(_convs(turned).dir) / "docs" / cid).glob("*.orig"))) == 1


def test_a_symlink_to_an_identical_copy_is_still_refused_because_links_are_never_followed(turned, tmp_path):
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    doc = _doc(client, token, cid, "same bytes", "same.txt")
    path = _orig_path(turned, cid, doc)
    twin = tmp_path / "twin"
    twin.write_bytes(path.read_bytes())                  # the hash would match: only refusing links stops this
    path.unlink()
    os.symlink(twin, path)
    assert _download(client, cid, doc).status_code == 404
