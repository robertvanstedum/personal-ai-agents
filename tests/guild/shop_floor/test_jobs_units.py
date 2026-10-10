"""Master Craftsman jobs: the pure pieces (commands, the proposal parser, the relay client's reading of answers)."""
from __future__ import annotations

import json
import threading

import pytest

from fake_job_relay import FakeJobRelay, Resp

from minimoi_portal.guild_ui import jobs as J
from minimoi_portal.guild_ui.mc.job_relay import JobRelay, clean_status


@pytest.mark.parametrize("text,expected", [
    ("stop", "stop"), ("Stop.", "stop"), ("  STOP  THE   JOB!! ", "stop"), ("cancel the job", "stop"), ("stop it", "stop"),
    ("Please run this as a job", "run_job"), ("run this as a job.", "run_job"), ("Compare my resumes. Run it as a job!", "run_job"),
    ("RUN THAT AS A JOB", "run_job"),
    ("please stop using jargon", None), ("stop the build and tell me why", None), ("what is a job?", None),
    ("should I run this as a job or not", "run_job"),            # an explicit phrase is honoured: the owner can say "no" by stopping
    ("run this as a joby thing", None), ("", None), (None, None),
])
def test_spoken_commands_are_decided_by_fixed_rules(text, expected):
    assert J.interpret(text) == expected


@pytest.mark.parametrize("text,clean,title", [
    ("JOB: Compare the two resumes\nI'll start that in the background.", True, "Compare the two resumes"),
    ("JOB: Compare the two resumes", True, "Compare the two resumes"),
    ("JOB: x\r\nbody", True, "x"),
    ("Sure!\nJOB: Compare", True, None),                          # not the first line
    ("JOB: Compare", False, None),                                # the stream did not end cleanly
    ("job: Compare", True, None), (" JOB: Compare", True, None), ("JOB:Compare", True, None), ("JOB: ", True, None),
    ("JOB: " + "x" * 81, True, None),                             # a title past 80 characters is text, not a trigger
    ("JOB: bad\x07title", True, None), ("", True, None), (None, True, None),
    ("```\nJOB: Compare\n```", True, None),                       # quoted, not a first line
])
def test_a_job_proposal_is_only_the_first_line_of_a_clean_stream(text, clean, title):
    got = J.parse_proposal(text, ended_cleanly=clean)
    assert (got["title"] if got else None) == title


def test_only_one_proposal_counts_and_the_rest_is_just_text():
    got = J.parse_proposal("JOB: First\nJOB: Second\nMore", ended_cleanly=True)
    assert got["title"] == "First" and got["rest"].startswith("JOB: Second")


def test_ids_come_from_the_note_and_never_collide_by_accident():
    assert J.job_id_for("note-a") == J.job_id_for("note-a") and J.job_id_for("note-a") != J.job_id_for("note-b")
    assert J.JOB_RE.fullmatch(J.job_id_for("x")) and J.result_request_id("j-0123456789abcdef").startswith("jobres-")
    assert J.result_request_id("j-0123456789abcdef") != J.job_id_for("j-0123456789abcdef")


@pytest.mark.parametrize("text", ["short", "a" * 3600, "para one\n\npara two " * 400, "word " * 2000, "x" * 10000], ids=['short', '3600-chars', 'paragraphs', 'words', '10000-chars'])
def test_the_note_part_of_a_result_always_fits_the_note_limit(text):
    shown = J.shown_in_note(text)
    assert 0 < len(shown) <= 4000
    if len(text) <= J.NOTE_SHOWN_MAX:
        assert shown == text
    else:
        assert shown.endswith('use "Open full result".]') and f"{len(text):,}" in shown


def test_default_titles_are_short_plain_and_free_of_the_command_phrase():
    assert J.default_title("Compare my resumes. Run this as a job.") == "Compare my resumes"
    assert J.default_title("run this as a job") == "Job" and len(J.default_title("word " * 100)) <= 80
    assert "<" not in J.default_title("<script>alert(1)</script> do it")


# ── reading the relay's answers ──────────────────────────────────────────────────

def test_clean_status_keeps_only_what_it_understands_and_bounds_everything():
    assert clean_status(None) is None and clean_status({"state": "weird"}) is None and clean_status([]) is None
    got = clean_status({"state": "completed", "elapsed_s": 12, "result": {"text": "ok\x00\x07 done"},
                        "audit": {"tools": [{"name": "read", "target": "a" * 999, "ok": True}] * 500, "truncated": False},
                        "writes": [{"path": "p", "exists": True, "bytes": 3, "sha256": "z" * 64, "verified": True},
                                   {"path": "q", "exists": 1, "bytes": -5, "sha256": "b" * 64, "verified": "yes"}], "error_class": None})
    assert got["result_text"] == "ok done" and len(got["tools"]) == 200 and got["tools_truncated"] and len(got["tools"][0]["target"]) == 300
    assert got["writes"][0]["sha256"] == "" and got["writes"][1]["bytes"] is None and got["writes"][1]["sha256"] == "b" * 64
    assert got["elapsed_s"] == 12


def _relay(fake):
    return JobRelay("http://relay/v1", "tok", http_get=fake.get, http_post=fake.post)


def test_a_start_is_classified_without_ever_being_retried():
    fake = FakeJobRelay()
    r = _relay(fake)
    spec = {"job_id": "j-0123456789abcdef", "agent": "mc-agent", "task": "t", "inbox": None, "deadline_s": 10}
    assert r.start(spec)[0] == "accepted"
    assert r.start(spec)[0] == "duplicate"
    for mode, expected in (("busy", "busy"), ("refuse", "refused"), ("server_error", "ambiguous"), ("ambiguous_lost", "ambiguous"), ("down", "ambiguous")):
        fake.start_mode = mode
        assert r.start({**spec, "job_id": "j-1111111111111111"})[0] == expected, mode
    assert len(fake.starts) == 1
    assert JobRelay("", "", http_get=fake.get, http_post=fake.post).start(spec) == ("refused", {"reason": "not_connected"})


def test_a_relay_answer_that_is_not_json_or_has_a_surprising_shape_is_never_trusted():
    class Weird:
        def __init__(self, code, body=None):
            self.code, self.body = code, body

        def __call__(self, *a, **k):
            return Resp(self.code, self.body) if self.body is not None else type("R", (), {"status_code": self.code, "json": lambda s: (_ for _ in ()).throw(ValueError())})()
    for code, body in ((200, {"accepted": "yes"}), (200, ["x"]), (500, None), (302, None), (200, None)):
        r = JobRelay("http://relay/v1", "tok", http_get=Weird(code, body), http_post=Weird(code, body))
        assert r.start({"job_id": "j"})[0] in ("ambiguous", "refused")
        assert r.status("j-0123456789abcdef")[0] in ("unreachable",)
        assert r.stop("j-0123456789abcdef") in ("unreachable",)


def test_the_relay_url_cannot_be_steered_by_a_job_id():
    seen = []
    r = JobRelay("http://relay/v1", "tok", http_get=lambda url, **k: seen.append(url) or Resp(404, {}), http_post=lambda *a, **k: Resp(404, {}))
    r.status("../../admin?x=1")
    assert seen == ["http://relay/v1/jobs/..%2F..%2Fadmin%3Fx%3D1"]


def test_the_ticker_runs_passes_until_stopped_and_survives_an_error():
    calls = []
    boom = {"n": 0}

    class M:
        wake = threading.Event()

        def tick(self):
            calls.append(1)
            boom["n"] += 1
            if boom["n"] == 1:
                raise RuntimeError("one bad pass")
    t = J.Ticker(M(), interval_s=0.01)
    t.start()
    for _ in range(200):
        if len(calls) >= 3:
            break
        threading.Event().wait(0.01)
    t.stop()
    t.join(2)
    assert len(calls) >= 3 and not t.is_alive()


def test_clean_status_keeps_only_the_two_flags_the_relay_may_raise_and_the_audit_availability():
    got = clean_status({"state": "running", "audit": {"tools": [{"name": "r", "target": "t", "flag": "touches_restricted_area"}, {"name": "w", "target": "t", "flag": "write_outside_allowed_area"},
                                                                  {"name": "x", "target": "t", "flag": "<script>"}, {"name": "y", "target": "t"}], "available": False}})
    assert [t.get("flag") for t in got["tools"]] == ["touches_restricted_area", "write_outside_allowed_area", None, None]
    assert got["audit_available"] is False and clean_status({"state": "running"})["audit_available"] is True


def test_a_long_job_title_is_cut_at_a_word_with_an_ellipsis_and_a_short_one_is_untouched():
    from minimoi_portal.guild_ui.jobs import TITLE_MAX, default_title
    short = default_title("Compare the two resumes")
    assert short == "Compare the two resumes"
    long = default_title("Compare the two resume files and write the differences into a readable summary note for the owner to review later")
    assert len(long) <= TITLE_MAX and long.endswith("…")
    assert not long[:-1].endswith(" ") and long[:-1] in "Compare the two resume files and write the differences into a readable summary note for the owner to review later"
    assert long[:-1].split(" ")[-1] in "Compare the two resume files and write the differences into a readable summary note for the owner to review later".split(" ")
