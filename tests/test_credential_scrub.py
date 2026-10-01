"""utils/credential_scrub.py: Spec 160 §3.4's credential list (review F3)."""
import re
import time
from pathlib import Path

import pytest

from utils.credential_scrub import REMOVED, SECRET, scrub

REPO = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("text,gone", [
    ("password: hunter2", "hunter2"),
    ("my password is hunter2", "hunter2"),
    ("pw=abc123", "abc123"),
    ("the secret is opensesame", "opensesame"),
    ("token: abcdef", "abcdef"),
    ("api key = k-123", "k-123"),
    ("Authorization: Bearer abc.def.ghi", "abc.def.ghi"),
    ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.sig", "eyJhbGci"),
    ("ghp_abcdefghijklmnop", "ghp_abcdefghijklmnop"),
    ("github_pat_11ABCDEF_xyz", "github_pat_11ABCDEF_xyz"),
    ("sk-ant-api03-abcdefghijkl", "sk-ant-api03"),
    ("sk-proj-abcdefghijkl", "sk-proj-abcdefghijkl"),
    ("xai-abcdefghijklmnopqrstuv", "xai-abcdefghijklmnopqrstuv"),
    ("tvly-abcdefgh12", "tvly-abcdefgh12"),
    ("AKIAABCDEFGHIJKLMNOP", "AKIAABCDEFGHIJKLMNOP"),
    ("-----BEGIN PRIVATE KEY-----\nMIIEv\n-----END PRIVATE KEY-----", "MIIEv"),
    ("bot 123456789:ABCdefGHIjklMNOpqrSTUvwxYZ012345678", "ABCdefGHIjklMNOpqrSTUvwxYZ012345678"),
    ("postgres://bob:pw1@db:5432/x", "bob:pw1"),
])
def test_each_credential_shape_is_removed(text, gone):
    out, changed = scrub(text)
    assert changed and gone not in out and REMOVED in out
    assert SECRET.search(text)


@pytest.mark.parametrize("text", [
    "the output tokens were 500",
    "a secret santa at work",
    "call me at 3 pm about the key results",
    "see https://example.com/path",
])
def test_ordinary_speech_is_kept(text):
    assert scrub(text) == (text, False)


def test_the_url_keeps_its_scheme_and_host():
    assert scrub("postgres://bob:pw1@db:5432/x")[0] == f"postgres://{REMOVED}@db:5432/x"


def test_a_key_after_a_keyword_is_removed_once():
    assert scrub("token: sk-abcdefghijk123")[0] == f"token: {REMOVED}"


def test_it_covers_the_workshops_secret_patterns():
    """Every alternative of the Workshop's _SECRET (#266, minimoi_portal/
    workshop/record.py) is caught here, so that module can import this one."""
    samples = ["sk-abcdefgh", "xai-abcdefghijklmnop", "tvly-abcdefgh", "ghp_abcdefghij", "github_pat_",
               "bearer x", "-----BEGIN", "eyJabcdefghijklmnopqrst.", "AKIAABCDEFGHIJKL", "https://u:p@h"]
    for sample in samples:
        assert SECRET.search(sample), sample
        assert scrub(sample)[1], sample


def test_the_worst_case_is_fast():
    text = ("password is x " * 600 + "sk-" + "a" * 3000 + " -----BEGIN " + "b" * 3000)[:16000]
    start = time.perf_counter()
    scrub(text)
    assert time.perf_counter() - start < 0.05                  # Spec 160: 16,000 characters in under 50 ms
