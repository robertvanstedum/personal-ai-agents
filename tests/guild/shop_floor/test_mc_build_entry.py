"""#693, the portal side: a build job's brief is the design Robert approved at its exact revision with his own note, it
gets 60 minutes (research keeps 30), a design that changed after the card starts nothing, the result note leads with
what the runner recorded, and Master Craftsman can read a delegated job of its own conversation and no other.

Real stores (conversations, notes, job files) and the real job manager with a fake clock; the delegate and the working
document store are stood in for. No model, no network."""
from __future__ import annotations

import hashlib

import pytest

from floor_helpers import load_portal, write_headers  # noqa: F401
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401
from test_mc_turns import turned  # noqa: F401
from test_jobs_engine import World
from test_mc_upload_reading import _new

from core.mc_tools import build_brief
from minimoi_portal.guild_ui import api, jobs as J
from minimoi_portal.guild_ui.mc.job_relay import clean_detail, clean_status

ART = "wa-" + "ab" * 16
DESIGN = "## Design\n\nAdd a `--dry-run` flag to the planning mirror.\n\n1. Parse the flag.\n2. Print, do not write.\n"
NOTE = "Please build the dry-run flag we designed for the planning mirror."
# The runner's result_detail for a build whose agent run completed (core/mc_delegation_runner, #693).
DETAIL = {"exit_code": 0, "base_ref": "a" * 40, "branch": "mc/1a2b3c4d-g1", "commit": "c" * 40, "changed": True,
          "tests": {"command": ["pytest", "tests/test_planning_mirror.py", "-q"], "exit_code": 0,
                    "summary": "18 passed in 2.1s", "duration_s": 2.1, "timed_out": False},
          "publish": {"state": "published", "pr_url": "https://github.com/example/repo/pull/7", "pr_number": 7},
          "pr_url": "https://github.com/example/repo/pull/7", "cost_usd": 1.2}
FOOTER = ("--- Delegated build ---\nBranch: mc/1a2b3c4d-g1\nCommit: " + "c" * 40 + "\nChanged: yes\n"
          "Tests: pytest tests/test_planning_mirror.py -q | exit 0 | 18 passed in 2.1s\n"
          "Pull request: https://github.com/example/repo/pull/7")


class Delegate:
    """The DelegationRelay surface, with the runner's recorded detail (core/mc_delegation_dispatcher/relay.py)."""

    connected = True

    def __init__(self):
        self.specs, self.jobs = [], {}

    def start(self, spec):
        self.specs.append(spec)
        self.jobs[spec["job_id"]] = {"state": "running", "text": None, "detail": None}
        return "accepted", {}

    def status(self, job_id):
        j = self.jobs.get(job_id)
        if j is None:
            return "not_found", None
        return "ok", clean_status({"state": j["state"], "elapsed_s": 1, "heartbeat_age_s": 0,
                                   "result": {"text": j["text"]} if j["text"] else None, "detail": j["detail"],
                                   "audit": {"available": False}, "writes": [], "error_class": None})

    def stop(self, job_id):
        self.jobs[job_id]["state"] = "stopped"
        return "confirmed"

    def finish(self, job_id, text, detail=None):
        self.jobs[job_id].update(state="completed", text=text, detail=detail)


class Documents:
    """The working-document store as the MC seat reads it (TasksAdapter.read_design)."""

    def __init__(self, content=DESIGN, revision=3):
        self.content, self.revision, self.current, self.title, self.down, self.reads = content, revision, revision, "Planning mirror dry run", False, []

    def read_design(self, artifact_id, revision):
        self.reads.append((artifact_id, revision))
        if self.down:
            raise RuntimeError("task service down")
        if artifact_id != ART:
            return {"status": "not_found", "source": "canonical"}
        return {"status": "found", "artifact_id": ART, "title": self.title, "revision": revision, "author": "mc",
                "content": self.content, "superseded": revision != self.current, "current_revision": self.current}


@pytest.fixture
def w(turned, tmp_path, monkeypatch):
    world = World(turned, tmp_path)
    world.delegate = Delegate()
    world.manager.relay = world.delegate
    world.docs = Documents()
    monkeypatch.setattr("minimoi_portal.guild_ui.jobs_api.manager", lambda: world.manager)
    monkeypatch.setattr("minimoi_portal.guild_ui.mc_tools_wiring.tasks_adapter", lambda environ: world.docs)
    return world


def proposal(w, note, **over):
    payload = {"agent": "claude-code", "title": "Dry-run flag", "design_artifact_id": ART, "design_revision": 3,
               "design_title": "Planning mirror dry run", "design_sha256": hashlib.sha256(DESIGN.encode()).hexdigest(),
               "design_chars": len(DESIGN)}
    return {"conversation_id": w.cid, "owner_turn_id": note["request_id"], "kind": "delegate_build",
            "payload": {**payload, **over}}


def found(w, note):
    return (w.services, w.principal, w.conv, w.manager.floor_for(w.conv), note, note["request_id"], "r-1", [])


def start(w, note=None, scope="build", **over):
    note = note or w.note(NOTE)
    return api._start_proposed_research(found(w, note), proposal(w, note, **over), scope=scope), note


# ── the brief ────────────────────────────────────────────────────────────────────

def test_the_build_brief_is_his_note_and_the_exact_design_and_the_job_names_what_it_was_built_from(w):
    (state, jid), note = start(w)
    assert state == "started" and w.docs.reads == [(ART, 3)]                  # the stored revision, read again at his yes
    digest = hashlib.sha256(DESIGN.encode()).hexdigest()
    job = w.manager.store.get(w.cid, jid)
    assert (job["scope"], job["agent"], job["title"]) == ("build", "claude-code", "Claude Code: Dry-run flag")
    assert (job["design_artifact_id"], job["design_revision"], job["design_sha256"]) == (ART, 3, digest)
    assert job["note_id"] == note["request_id"] and jid == J.job_id_for(note["request_id"])
    w.manager.tick()
    [spec] = w.delegate.specs
    brief = spec["task"]
    assert brief == (build_brief.HEADER + "\n\n--- Robert's message ---\n" + NOTE + "\n\n"
                     f"--- Approved design: Planning mirror dry run (revision 3, sha256 {digest}) ---\n" + DESIGN)
    assert job["task_sha256"] == hashlib.sha256(brief.encode()).hexdigest()     # the digest covers the whole brief
    assert (spec["scope"], spec["agent"]) == ("build", "claude-code")
    assert (spec["design_artifact_id"], spec["design_revision"], spec["design_sha256"]) == (ART, 3, digest)


def test_a_build_gets_60_minutes_and_research_keeps_30(w):
    (_, build_id), _ = start(w)
    research_note = w.note("Ask about the mirror")
    other = _new(w.client, w.token)                                             # one running job per conversation
    conv = w.convs.get(other, w.principal)
    n2 = w.note("Which modules read the planning mirror?", cid=other)
    research, refusal = w.manager.start(conv, w.principal, n2, n2["request_id"], None, agent="claude-code", scope="research")
    assert refusal is None and research_note
    assert w.manager.store.get(w.cid, build_id)["deadline_s"] == 60 * 60 == J.BUILD_DEADLINE_S
    assert research["deadline_s"] == 30 * 60
    w.manager.tick()
    sent = {s["job_id"]: s for s in w.delegate.specs}
    assert sent[build_id]["deadline_s"] == 3600 and sent[research["id"]]["deadline_s"] == 1800
    assert "design_sha256" not in sent[research["id"]] and sent[research["id"]]["task"] == "Which modules read the planning mirror?"
    # The portal's own clock asks for a stop only after the job's limit: 59 minutes in, the build still runs.
    w.clock.advance(59 * 60)
    w.manager.tick()
    assert w.manager.store.get(w.cid, build_id)["state"] == "running" and w.delegate.jobs[build_id]["state"] == "running"
    assert w.manager.store.get(other, research["id"])["state"] == "stopped"     # research was stopped at its 30
    w.clock.advance(2 * 60)
    w.manager.tick()
    assert w.manager.store.get(w.cid, build_id)["stop_reason"] == "deadline"


# ── a design that is no longer the one on the card starts nothing ─────────────────────

def nothing_queued(w):
    return w.manager.store.list(w.cid) == [] and w.delegate.specs == []


def test_a_design_whose_text_differs_from_the_card_is_refused_and_nothing_is_queued(w):
    w.docs.content = DESIGN + "\n3. Also delete the old mirror.\n"
    (state, jid), _ = start(w)
    assert (state, jid) == ("design_changed", None)
    w.manager.tick()
    assert nothing_queued(w)


def test_a_newer_revision_saved_after_the_card_is_refused_and_nothing_is_queued(w):
    w.docs.current = 4                                                          # revision 3 is still there, but superseded
    assert start(w)[0] == ("design_changed", None)
    w.manager.tick()
    assert nothing_queued(w)


@pytest.mark.parametrize("change", ["gone", "title", "old_card"])
def test_a_missing_document_a_changed_title_or_a_card_without_a_design_starts_nothing(w, change):
    over = {}
    if change == "gone":
        over = {"design_artifact_id": "wa-" + "cd" * 16}
    elif change == "title":
        w.docs.title = "Something else"
    else:
        over = {"design_sha256": None}
    (state, jid), _ = start(w, **over)
    assert (state, jid) == ("refused" if change == "old_card" else "design_changed", None) and nothing_queued(w)


def test_a_store_that_cannot_be_read_starts_nothing_and_is_not_called_a_change(w):
    w.docs.down = True
    assert start(w)[0] == ("design_unavailable", None) and nothing_queued(w)


def test_the_note_must_be_the_owners_own_and_the_conversation_the_proposals(w):
    note = w.note(NOTE)
    p = proposal(w, note)
    assert api._start_proposed_research(found(w, note), {**p, "conversation_id": "c-000000000000"}, scope="build") == ("refused", None)
    assert api._start_proposed_research(found(w, note), {**p, "owner_turn_id": "n-not-a-note"}, scope="build") == ("refused", None)
    assert nothing_queued(w) and w.docs.reads == []


def test_the_same_note_never_starts_a_second_job_even_if_the_design_changes_afterwards(w):
    (state, jid), note = start(w)
    w.docs.content = "A different design."
    (again, same), _ = start(w, note)
    assert (state, again) == ("started", "started") and same == jid and len(w.manager.store.list(w.cid)) == 1
    w.manager.tick()
    assert len(w.delegate.specs) == 1 and DESIGN in w.delegate.specs[0]["task"]   # the brief approved first, unchanged


def test_a_brief_changed_on_disk_after_the_yes_is_not_sent(w):
    (_, jid), _ = start(w)
    w.manager.store.write_brief(w.cid, jid, "Delete everything.")
    w.manager.tick()
    job = w.manager.store.get(w.cid, jid)
    assert (job["state"], job["error_class"]) == ("not_started", "brief_missing") and w.delegate.specs == []
    assert "could not be read back unchanged" in w.manager.view(job)["message"]


def test_a_brief_over_the_envelopes_limit_or_without_its_design_is_refused_by_the_manager(w):
    note = w.note(NOTE)
    design = {"artifact_id": ART, "revision": 3, "sha256": "a" * 64, "title": "T"}
    for kwargs, code in (({"brief": "x" * 16_001, "design": design}, "brief_too_large"),
                         ({"brief": "x", "design": None}, "brief_invalid"),
                         ({"brief": None, "design": design}, "brief_invalid"),
                         ({"brief": "x", "design": {**design, "revision": "3"}}, "brief_invalid")):
        job, refusal = w.manager.start(w.conv, w.principal, note, note["request_id"], None, agent="claude-code", scope="build", **kwargs)
        assert job is None and refusal[0] == code, kwargs
    job, refusal = w.manager.start(w.conv, w.principal, note, note["request_id"], None, agent="claude-code",
                                   scope="research", brief="x", design=design)
    assert job is None and refusal[0] == "brief_invalid" and nothing_queued(w)   # only a build carries a brief


# ── the result comes back with substance ──────────────────────────────────────────────

def agent_notes(w):
    return [r["text"] for r in w.notes() if r["author_kind"] == "agent"]


def test_a_build_result_note_leads_with_what_the_runner_recorded(w):
    (_, jid), _ = start(w)
    w.manager.tick()
    w.delegate.finish(jid, "I added the flag and a test.\n\n" + FOOTER, DETAIL)
    w.manager.tick()
    job = w.manager.store.get(w.cid, jid)
    assert job["state"] == "completed" and job["result_detail"] == clean_detail(DETAIL)
    [text] = agent_notes(w)
    assert text == ("Build result, as the runner recorded it:\n"
                    "- Branch: mc/1a2b3c4d-g1 at commit cccccccccccc\n"
                    "- Changed files: yes\n"
                    "- Tests (run by the runner): `pytest tests/test_planning_mirror.py -q`, exit code 0. 18 passed in 2.1s\n"
                    "- Draft pull request: https://github.com/example/repo/pull/7\n"
                    "Nothing was merged, released or deployed.\n\n"
                    "I added the flag and a test.")                    # the footer is not said twice
    assert w.manager.store.read_result(w.cid, jid) == "I added the flag and a test.\n\n" + FOOTER   # the kept result is whole
    assert job["result_in_note_cut"] is False


def test_an_unpublished_build_says_so_with_the_reason_and_a_missing_test_run_is_said_as_missing(w):
    (_, jid), _ = start(w)
    w.manager.tick()
    w.delegate.finish(jid, "Done.", {"branch": "mc/1a2b3c4d-g1", "commit": None, "changed": False,
                                     "tests": {"command": None, "exit_code": None, "summary": "", "duration_s": 0.0,
                                               "timed_out": False, "note": "no_changes"},
                                     "publish": {"state": "skipped", "code": "publish_not_configured"}})
    w.manager.tick()
    [text] = agent_notes(w)
    assert "- Branch: mc/1a2b3c4d-g1, no commit" in text and "- Changed files: none" in text
    assert "- Tests: not run by the runner (no changes)." in text
    assert "- Pull request: not published (publish not configured)." in text and "Draft pull request" not in text


def test_without_the_structured_record_the_runners_footer_leads_the_note_and_is_what_mc_reads(w):
    """Today DelegationRelay.status hands over the text only: the runner's own footer, at the very end, is the record."""
    (_, jid), _ = start(w)
    w.manager.tick()
    w.delegate.finish(jid, "I added the flag.\n\nBranch: the agent's own words stay where they are.\n\n" + FOOTER)
    w.manager.tick()
    [text] = agent_notes(w)
    lines = FOOTER.split("\n")[1:]
    assert text == ("Build result, as the runner recorded it:\n" + "\n".join("- " + line for line in lines)
                    + "\nNothing was merged, released or deployed.\n\n"
                    "I added the flag.\n\nBranch: the agent's own words stay where they are.")
    got = w.manager.report(w.cid, w.principal)
    assert got["summary_lines"] == lines and got["detail"] is None and got["text"].endswith(FOOTER)


def test_a_footer_that_is_not_the_runners_exact_last_six_lines_is_not_taken_for_one():
    lines, rest = J.build_footer("Text.\n\n" + FOOTER + "\n")
    assert lines == FOOTER.split("\n")[1:] and rest == "Text."
    for text in (FOOTER + "\n\nAnd then the agent wrote more.", FOOTER.replace("Changed: yes\n", ""),
                 FOOTER.replace("--- Delegated build ---", "--- Build ---"), "No footer at all.", ""):
        assert J.build_footer(text) == (None, text)
    unpublished = FOOTER.replace("Pull request: https://github.com/example/repo/pull/7", "Pull request: not published: publish_not_configured")
    assert J.build_footer(unpublished)[0][-1] == "Pull request: not published: publish_not_configured"


def test_without_runner_detail_the_text_is_shown_as_it_is_and_research_never_gets_a_build_summary(w):
    (_, jid), _ = start(w)
    w.manager.tick()
    w.delegate.finish(jid, "Built it; see the patch.")
    w.manager.tick()
    assert agent_notes(w) == ["Built it; see the patch."]
    other = _new(w.client, w.token)
    n2 = w.note("Which modules read the planning mirror?", cid=other)
    research, _ = w.manager.start(w.convs.get(other, w.principal), w.principal, n2, n2["request_id"], None, agent="claude-code")
    w.manager.tick()
    w.delegate.finish(research["id"], "Two modules read it.\n\n" + FOOTER, DETAIL)   # neither is a build's on research
    w.manager.tick()
    got = w.manager.report(other, w.principal)
    assert "Two modules read it.\n\n" + FOOTER in agent_notes(w) and got["detail"] is None and got["summary_lines"] is None


def test_a_long_build_result_keeps_the_summary_and_cuts_the_text_within_the_notes_limit(w):
    (_, jid), _ = start(w)
    w.manager.tick()
    w.delegate.finish(jid, "word " * 2000, DETAIL)
    w.manager.tick()
    [text] = agent_notes(w)
    assert text.startswith("Build result, as the runner recorded it:") and len(text) <= 4000
    assert "The full result (10,000 characters) is kept with this job" in text
    assert w.manager.store.get(w.cid, jid)["result_in_note_cut"] is True


def test_runner_detail_is_kept_only_in_the_shape_it_should_have():
    assert clean_detail(None) is None and clean_detail({}) is None and clean_detail({"branch": "x y; rm -rf"}) is None
    assert clean_detail({"exit_code": 0, "base_ref": "a" * 40}) is None          # research, or a build that did not complete
    got = clean_detail({"branch": "mc/j-1", "commit": "zz", "pr_url": "https://evil.example/pull/1", "changed": 4,
                        "tests": {"command": "pytest\x00 -q", "exit_code": True, "summary": "ok\nline", "timed_out": 1, "note": "made up"},
                        "publish": {"state": "Failed!", "code": "x"}, "extra": "ignored"})
    assert got == {"branch": "mc/j-1",
                   "tests": {"command": "pytest -q", "exit_code": None, "summary": "ok line", "duration_s": None, "timed_out": False}}
    # A link is kept only for a build the runner says it published, and only a GitHub pull request link.
    assert "pr_url" not in clean_detail({"branch": "mc/j-1", "publish": {"state": "pr_failed", "code": "http_422"},
                                         "pr_url": "https://github.com/example/repo/pull/7"})
    assert clean_detail(DETAIL)["pr_url"] == "https://github.com/example/repo/pull/7"
    assert clean_detail(DETAIL)["tests"]["command"] == "pytest tests/test_planning_mirror.py -q"
    assert clean_status({"state": "running"})["detail"] is None


# ── what Master Craftsman may read back ─────────────────────────────────────────────

def test_the_report_is_this_conversations_delegated_job_with_its_design_detail_and_text(w):
    assert w.manager.report(w.cid, w.principal) is None                         # nothing delegated here yet
    (_, jid), _ = start(w)
    running = w.manager.report(w.cid, w.principal)
    assert (running["job_id"], running["state"], running["active"], running["text"]) == (jid, "queued", True, None)
    w.manager.tick()
    w.delegate.finish(jid, "I added the flag.", DETAIL)
    w.manager.tick()
    for got in (w.manager.report(w.cid, w.principal), w.manager.report(w.cid, w.principal, jid)):   # the latest, and by id
        assert (got["state"], got["scope"], got["agent"], got["title"]) == ("completed", "build", "claude-code", "Claude Code: Dry-run flag")
        assert got["design"] == {"artifact_id": ART, "revision": 3, "sha256": hashlib.sha256(DESIGN.encode()).hexdigest(),
                                 "title": "Planning mirror dry run"}
        assert got["detail"] == clean_detail(DETAIL) and got["text"] == "I added the flag."
        assert got["started_at"] and got["finished_at"] >= got["started_at"]


def test_the_report_never_returns_another_conversations_owners_or_mcs_own_job(w):
    (_, jid), _ = start(w)
    other = _new(w.client, w.token)
    assert w.manager.report(other, w.principal) is None and w.manager.report(other, w.principal, jid) is None
    assert w.manager.report(w.cid, "someone-else") is None and w.manager.report(w.cid, "someone-else", jid) is None
    assert w.manager.report("../" + w.cid, w.principal) is None and w.manager.report(w.cid, w.principal, "../x") is None
    n = w.note("Compare my resumes.", cid=other)                                # MC's own job is not a delegated one
    own, _ = w.manager.start(w.convs.get(other, w.principal), w.principal, n, n["request_id"])
    assert own["agent"] == "mc-agent" and w.manager.report(other, w.principal) is None
    assert w.manager.report(other, w.principal, own["id"]) is None


def test_the_wired_reader_asks_the_mounts_manager_for_the_tool_owners_jobs_only(w):
    from minimoi_portal.guild_ui.mc_tools_wiring import delegation_job_reader
    (_, jid), _ = start(w)
    app = type("App", (), {"extensions": {"guild_ui_next": {"jobs": w.manager}}})()
    read = delegation_job_reader(app, "guild_ui_next", w.principal)
    assert read(w.cid)["job_id"] == jid and read(w.cid, jid)["job_id"] == jid and read("c-000000000000") is None
    assert delegation_job_reader(app, "guild_ui_next", "someone-else")(w.cid) is None
    app.extensions["guild_ui_next"].pop("jobs")
    with pytest.raises(LookupError):
        read(w.cid)
