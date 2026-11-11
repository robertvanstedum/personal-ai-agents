"""The workshop journal and the isolated host reader follow the shared credential list.

The host reader cannot import the shared module (it runs under python -I), so it
keeps its own expression. These strings fail if that copy drifts.
"""
import importlib.util
from pathlib import Path

import pytest

from core.workshop_journal.legacy import validate
from utils.credential_scrub import SECRET

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fixed_host_inspector", ROOT / "scripts/mc_host/inspect.py")
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)

# Shapes the shared list removes, at a length the host copy is also required to refuse.
SHARED = [
    "ghp_" + "a" * 10,
    "gho_" + "a" * 10,
    "ghs_" + "a" * 10,
    "ghu_" + "a" * 10,
    "ghr_" + "a" * 10,
    "github_pat_" + "A" * 16,
    "sk-ant-api03-abcdefghijkl",
    "xai-" + "a" * 16,
    "tvly-" + "a" * 8,
    "AKIAABCDEFGHIJKLMNOP",
    "-----BEGIN PRIVATE KEY-----",
    "Authorization: Bearer abc.def.ghi",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.sig",
]


def _event(text):
    return {"workshop": "shop", "actor": "robert", "kind": "progress", "item": "pr:1", "text": text}


def test_ordinary_workshop_text_is_still_accepted():
    assert validate(_event("a secret santa at work"))["text"] == "a secret santa at work"


@pytest.mark.parametrize("sample", SHARED)
def test_the_journal_refuses_what_the_shared_list_matches(sample):
    assert SECRET.search(sample)
    with pytest.raises(ValueError, match="looks like a credential"):
        validate(_event(sample))


@pytest.mark.parametrize("sample", SHARED)
def test_the_host_reader_matches_the_shared_list(sample):
    assert bool(SECRET.search(sample)) and bool(host.SECRET.search(sample))
