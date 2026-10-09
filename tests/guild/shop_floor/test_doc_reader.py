"""Upload reading, the reader (doc_reader.py): what a file becomes as text for Master Craftsman, and what is refused
and why. Synthetic files only. No model, no network."""
from __future__ import annotations

import subprocess
import sys

import pytest

from doc_fixtures import docx_bytes, docx_zip_bomb, encrypted_pdf_bytes, pdf_bytes
from minimoi_portal.guild_ui import doc_reader as dr
from minimoi_portal.guild_ui.doc_reader import Unreadable, extract, extract_safely, refusal_for


def _refused(raw, name):
    with pytest.raises(Unreadable) as e:
        extract(raw, name)
    return e.value


# ── text, code, html ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["notes.txt", "README.md", "data.csv", "x.json", "conf.yaml", "run.sh", "q.SQL", "a.py"])
def test_text_and_code_are_read_in_full(name):
    got = extract("alpha\r\nbeta\tgamma\n\n\n\ndelta".encode(), name)
    assert got["text"] == "alpha\nbeta\tgamma\n\ndelta" and got["kind"] == "text"
    assert got["truncated"] is False and got["chars_total"] == len(got["text"])


def test_a_utf8_bom_is_dropped_and_control_characters_are_removed():
    got = extract("﻿café \x07bell \x1b[31mred".encode(), "a.txt")
    assert got["text"] == "café bell [31mred" and "\x07" not in got["text"] and "\x1b" not in got["text"]


def test_binary_and_non_utf8_are_refused_with_a_reason():
    assert _refused(b"abc\x00def", "a.txt").code == "binary"
    assert _refused(bytes(range(1, 31)) * 40, "a.txt").code == "binary"
    assert _refused("café".encode("latin-1"), "a.txt").code == "not_utf8"
    assert _refused(b"   \n\n  ", "a.txt").code == "empty"


def test_html_keeps_the_text_and_drops_scripts_and_styles():
    raw = (b"<html><head><title>T</title><style>p{color:red}</style></head><body><h1>Plan</h1>"
           b"<script>alert('x')</script><p>Ship <b>Friday</b></p><noscript>enable js</noscript></body></html>")
    got = extract(raw, "page.html")
    assert "Plan" in got["text"] and "Ship" in got["text"] and "Friday" in got["text"]
    assert "alert" not in got["text"] and "color:red" not in got["text"] and "enable js" not in got["text"]
    assert got["kind"] == "html"


def test_a_long_text_is_cut_visibly_and_counted():
    got = extract(("line of text\n" * 5000).encode(), "big.txt")
    assert len(got["text"]) == dr.MAX_STORED_CHARS and got["truncated"] is True
    assert got["chars_total"] == len(("line of text\n" * 5000).strip("\n"))


# ── pdf ──────────────────────────────────────────────────────────────────────────

def test_a_pdf_text_layer_is_read_with_its_pages():
    got = extract(pdf_bytes(["Invoice total 42 EUR", "Second page: delivery Friday"]), "bill.pdf")
    assert "Invoice total 42 EUR" in got["text"] and "delivery Friday" in got["text"]
    assert got["kind"] == "pdf" and got["pages_read"] == 2 and got["pages_total"] == 2 and got["truncated"] is False


def test_a_scan_with_no_text_layer_is_reported_not_invented():
    err = _refused(pdf_bytes(["", ""]), "scan.pdf")
    assert err.code == "no_text" and "no text layer" in err.message


def test_an_encrypted_a_corrupt_and_a_disguised_pdf_are_refused():
    assert _refused(encrypted_pdf_bytes(), "locked.pdf").code == "encrypted"
    assert _refused(b"%PDF-1.4\nthis is not really a pdf", "broken.pdf").code == "corrupt"
    assert _refused(b"plain words, not a pdf", "fake.pdf").code == "corrupt"


def test_a_pdf_over_the_page_limit_reads_the_first_pages_and_says_so():
    got = extract(pdf_bytes([f"page number {i}" for i in range(1, 121)]), "long.pdf")
    assert got["pages_read"] == dr.MAX_PDF_PAGES and got["pages_total"] == 120 and got["truncated"] is True
    assert "page number 100" in got["text"] and "page number 101" not in got["text"]


# ── docx ─────────────────────────────────────────────────────────────────────────

def test_a_docx_body_is_read():
    got = extract(docx_bytes(["Heading one", "A second paragraph"]), "plan.docx")
    assert got["text"] == "Heading one\nA second paragraph" and got["kind"] == "docx"


def test_a_docx_zip_bomb_a_doctype_and_a_non_docx_are_refused():
    assert _refused(docx_zip_bomb(), "bomb.docx").code == "too_large"
    entity = docx_bytes(["x"], doctype='<!DOCTYPE d [<!ENTITY a "aaaa">]>')
    assert _refused(entity, "xxe.docx").code == "corrupt"
    assert _refused(b"not a zip", "a.docx").code == "corrupt"
    assert _refused(pdf_bytes(["hi"]), "pretend.docx").code == "corrupt"


# ── what cannot be read, said before anything is parsed ──────────────────────────

@pytest.mark.parametrize("name,code", [("pic.png", "image"), ("PHOTO.JPG", "image"), ("scan.heic", "image"),
                                       ("sheet.xlsx", "unsupported"), ("deck.pptx", "unsupported"),
                                       ("old.doc", "unsupported"), ("bundle.zip", "unsupported"),
                                       ("noextension", "unsupported")])
def test_images_and_other_formats_are_refused_plainly(name, code):
    code_, message = refusal_for(name)
    assert code_ == code
    assert (("can't see images" in message) if code == "image" else ("can't be read yet" in message))
    assert _refused(b"data", name).code == code


def test_a_file_over_the_size_limit_is_refused_before_it_is_read():
    assert _refused(b"a" * (dr.MAX_UPLOAD_BYTES + 1), "a.txt").code == "too_large"


# ── the child process: same answer, bounded ──────────────────────────────────────

def test_the_child_process_gives_the_same_answer_as_reading_in_process():
    raw = pdf_bytes(["Delivery Friday", "Second page"])
    assert extract_safely(raw, "a.pdf") == extract(raw, "a.pdf")
    assert extract_safely(b"hello\nthere", "a.txt")["text"] == "hello\nthere"


def test_the_child_process_reports_an_unreadable_file_as_the_same_error():
    with pytest.raises(Unreadable) as e:
        extract_safely(encrypted_pdf_bytes(), "locked.pdf")
    assert e.value.code == "encrypted"
    with pytest.raises(Unreadable) as e:
        extract_safely(b"x", "pic.png")
    assert e.value.code == "image"


def test_a_child_that_takes_too_long_is_stopped(monkeypatch):
    def hang(*a, **kw):
        raise subprocess.TimeoutExpired(a[0], kw.get("timeout"))
    monkeypatch.setattr(dr.subprocess, "run", hang)
    with pytest.raises(Unreadable) as e:
        extract_safely(b"hello", "a.txt")
    assert e.value.code == "timeout"


def test_a_child_that_crashes_or_prints_rubbish_is_a_plain_failure(monkeypatch):
    class Done:
        stdout = b"<<not json>>"
    monkeypatch.setattr(dr.subprocess, "run", lambda *a, **kw: Done())
    with pytest.raises(Unreadable) as e:
        extract_safely(b"hello", "a.txt")
    assert e.value.code == "failed"


def test_the_child_runs_with_a_clean_environment(monkeypatch):
    seen = {}

    class Done:
        stdout = b'{"ok": true, "text": "x", "kind": "text", "pages_read": null, "pages_total": null, "chars_total": 1, "truncated": false}'

    def fake(cmd, **kw):
        seen.update(kw, cmd=cmd)
        return Done()
    monkeypatch.setenv("MC_RUNTIME_TOKEN", "must-not-reach-the-child")
    monkeypatch.setenv("DATABASE_URL", "postgres://secret")
    monkeypatch.setattr(dr.subprocess, "run", fake)
    extract_safely(b"x", "a.txt")
    assert set(seen["env"]) <= {"PATH", "PYTHONDONTWRITEBYTECODE", "LANG"} and seen["cwd"] == "/"
    assert seen["cmd"][0] == sys.executable and seen["cmd"][1] == "-I" and "--child" in seen["cmd"]   # isolated: guild_ui/ is not on its path
    assert seen["timeout"] == dr.CHILD_TIMEOUT_S
