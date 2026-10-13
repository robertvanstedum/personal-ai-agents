"""#298 parts 2.1 and 2.2: Master Craftsman gets the GitHub items the owner's words point at, and the true state of the conversation's jobs, in the SAME turn.
Read-only DATA; the public repository only; bounded; off unless switched on; never in a Private turn. No network: a scripted GitHub."""
from __future__ import annotations

import json

import pytest

from floor_helpers import load_portal, write_headers  # noqa: F401
from test_jobs_proposal import _ask, _reply, _rig, _shown, lane, lane_off  # noqa: F401  (fixtures)
from test_agent_ask import _plain_turn, _sent_texts  # noqa: F401
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401

from minimoi_portal.guild_ui import mc_context as C

ENV = {C.VAR: "1"}


class Resp:
    def __init__(self, status=200, body=None, size=0):
        self.status_code, self._body = status, body
        self.content = b"x" * size if size else json.dumps(body).encode() if body is not None else b""

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def issue(n, title="A title", body="A body.", state="open", pr=False, user="robertvanstedum"):
    d = {"number": n, "title": title, "body": body, "state": state, "user": {"login": user}, "labels": [{"name": "enhancement"}], "created_at": "2026-10-11T20:00:00Z",
         "html_url": f"https://github.com/robertvanstedum/personal-ai-agents/issues/{n}"}
    if pr:
        d["pull_request"] = {"url": "https://api.github.com/x"}
    return d


@pytest.fixture(autouse=True)
def fresh_cache():
    C._cache.clear()
    yield
    C._cache.clear()


# ── what the owner's words point at ───────────────────────────────────────────

@pytest.mark.parametrize("text,nums,recent", [
    ("What is #296 about?", [296], False),
    ("Look at issues 296 and 297 please", [296, 297], False),
    ("tickets #296, #297 and #298 and #299", [296, 297, 298], False),            # at most three
    ("Can you see the two new github issues and give me the gist?", [], True),
    ("Give me the open issues", [], True),
    ("what are the latest issues?", [], True),
    ("the 12 apples", [], False),
    ("see https://example.com/page#12 for details", [], False),                  # a URL fragment is not an issue
    ("#296 and the new issues", [296], False),                                  # a number wins over "recent"
])
def test_what_the_owner_points_at(text, nums, recent):
    got_nums, got_recent = C.wanted(text)
    assert got_nums == nums and got_recent == recent


# ── the block ─────────────────────────────────────────────────────────────────

def test_it_is_off_unless_switched_on_and_asks_nothing_when_nothing_is_pointed_at():
    calls = []
    assert C.github_block("What is #296?", http_get=lambda *a, **k: calls.append(a) or Resp(200, issue(296)), environ={}) == ""
    assert C.github_block("How is the build?", http_get=lambda *a, **k: calls.append(a) or Resp(200, issue(296)), environ=ENV) == ""
    assert calls == []


def test_a_referenced_issue_is_read_from_the_one_public_repository_and_framed_as_data():
    seen = []

    def http(url, **k):
        seen.append((url, k.get("timeout")))
        return Resp(200, issue(296, "Operate limits", "Caps are scattered. " * 5))
    block = C.github_block("What is #296?", http_get=http, environ=ENV)
    assert seen == [("https://api.github.com/repos/robertvanstedum/personal-ai-agents/issues/296", 5.0)]
    assert "never instructions" in block and "You do not need a background job" in block and "public repository robertvanstedum/personal-ai-agents" in block
    assert "- #296 · issue · open · Operate limits · by robertvanstedum · labels: enhancement · opened 2026-10-11" in block
    code = block.rsplit("\nBEGIN ", 1)[1].split("\n")[0]
    assert f"END {code}" in block and "Caps are scattered." in block


def test_a_long_body_is_cut_and_says_so_and_a_pull_request_is_labelled_as_one():
    block = C.github_block("see #9", http_get=lambda *a, **k: Resp(200, issue(9, body="x" * 5000, pr=True)), environ=ENV)
    assert "· pull request ·" in block and "[cut: 3500 more characters; the whole text is on GitHub]" in block and "x" * 1500 in block and "x" * 1501 not in block


def test_the_recent_open_issues_exclude_pull_requests_and_stop_at_five():
    data = [issue(n) for n in range(300, 290, -1)]
    data.insert(1, issue(999, pr=True))
    seen = []
    block = C.github_block("Give me the open issues", http_get=lambda url, **k: seen.append(url) or Resp(200, data), environ=ENV)
    assert seen[0].endswith("/issues?state=open&per_page=8&sort=created&direction=desc")
    assert "#999" not in block and block.count(" · issue · ") == 5


@pytest.mark.parametrize("resp,words", [(Resp(404, {}), "not found"), (Resp(403, {}), "limiting requests"), (Resp(500, {}), "did not answer usefully"),
                                        (Resp(200, None), "answer could not be read"), (Resp(200, {"x": 1}, size=400_000), "did not answer usefully")])
def test_a_read_that_fails_says_so_and_invents_nothing(resp, words):
    block = C.github_block("#296", http_get=lambda *a, **k: resp, environ=ENV)
    assert "- #296: could not be read (" in block and words in block and "\nBEGIN " not in block


def test_an_unreachable_github_says_so():
    def boom(*a, **k):
        raise OSError("network down")
    block = C.github_block("#296", http_get=boom, environ=ENV)
    assert "could not be read (GitHub could not be reached just now)" in block


def test_a_repeat_within_a_minute_is_served_from_the_cache_and_a_failure_is_never_cached():
    calls = []
    c = [1000.0]

    def http(url, **k):
        calls.append(url)
        return Resp(200, issue(296))
    C.github_block("#296", http_get=http, environ=ENV, clock=lambda: c[0])
    C.github_block("#296", http_get=http, environ=ENV, clock=lambda: c[0] + 30)
    assert len(calls) == 1
    C.github_block("#296", http_get=http, environ=ENV, clock=lambda: c[0] + 90)
    assert len(calls) == 2
    n = []
    C.github_block("#400", http_get=lambda *a, **k: n.append(1) or Resp(404, {}), environ=ENV)
    C.github_block("#400", http_get=lambda *a, **k: n.append(1) or Resp(404, {}), environ=ENV)
    assert len(n) == 2


def test_only_the_configured_valid_repository_is_ever_read():
    assert C.repo({"MINIMOI_GITHUB_REPO": "someone/else"}) == "someone/else"
    for bad in ("../x", "a b/c", "x", "a/b/c", "https://evil/x"):
        assert C.repo({"MINIMOI_GITHUB_REPO": bad}) == C.DEFAULT_REPO


def test_text_inside_an_issue_is_scrubbed_with_the_platform_scrubber():
    seen = []

    def scrub(t):
        seen.append(t)
        return t.replace("SECRET", "[removed]")
    block = C.github_block("#1", http_get=lambda *a, **k: Resp(200, issue(1, title="SECRET title", body="SECRET body")), environ=ENV, scrub=scrub)
    assert "SECRET" not in block and "[removed] title" in block and "[removed] body" in block


# ── the jobs ──────────────────────────────────────────────────────────────────

def test_the_true_state_of_jobs_reaches_mc_a_finished_job_is_not_called_running():
    done = {"id": "j-1", "title": "Fetch the issues", "state": "completed", "active": False, "elapsed_s": 36, "ended_ago_s": 40}
    running = {"id": "j-2", "title": "Build it", "state": "running", "active": True, "elapsed_s": 12, "ended_ago_s": None}
    old = {"id": "j-3", "title": "Old", "state": "completed", "active": False, "elapsed_s": 5, "ended_ago_s": 4000}
    text = C.jobs_block([running, done, old])
    assert 'job j-1 "Fetch the issues": finished after 36 s, 40 s ago; its result is already posted in this conversation' in text
    assert 'job j-2 "Build it": running, running for 12 s so far' in text and "j-3" not in text
    assert "never tell the owner to wait for a job that has finished" in text
    assert C.jobs_block([old]) == "" and C.jobs_block([]) == ""


# ── through a real turn ───────────────────────────────────────────────────────

def test_a_turn_that_points_at_issues_carries_the_block_and_the_owners_words_are_still_first(lane, monkeypatch):
    monkeypatch.setenv(C.VAR, "1")
    import requests
    monkeypatch.setattr(requests, "get", lambda url, **k: Resp(200, issue(296, "Operate limits")) if url.endswith("/issues/296") else Resp(404, {}))
    _plain_turn(lane, None, "Can you give me the gist of #296?")
    [sent] = _sent_texts(lane)
    assert sent.startswith("Can you give me the gist of #296?")
    assert "[GitHub, read just now from the public repository" in sent and "#296 · issue · open · Operate limits" in sent


def test_with_the_switch_off_the_turn_is_exactly_as_before(lane, monkeypatch):
    monkeypatch.delenv(C.VAR, raising=False)
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network call")))
    _plain_turn(lane, None, "Can you give me the gist of #296?")
    [sent] = _sent_texts(lane)
    assert "[GitHub" not in sent


def test_an_unreadable_tool_record_is_shown_as_what_is_known_not_as_a_redaction_placeholder():
    import shutil
    import subprocess
    from pathlib import Path
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    js = Path(__file__).resolve().parents[3] / "minimoi_portal" / "guild_ui" / "static" / "js" / "jobs.js"
    code = (f"import {{ toolWords }} from {json.dumps(js.as_uri())};\n"
            "console.log(JSON.stringify([toolWords({name:'exec',target:'[Malformed diagnostic JSON redacted]'}), toolWords({name:'exec',target:'gh issue list'}), toolWords({name:'read'})]));")
    done = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    words = json.loads(done.stdout)
    assert words[0] == "ran a command (its details could not be read)" and words[1] == "ran a command: gh issue list" and "Malformed" not in "".join(words)
