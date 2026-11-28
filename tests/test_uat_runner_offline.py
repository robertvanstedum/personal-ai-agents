"""The automatic UAT runner (tests/uat/staging_uat.py), offline: no network, no browser, no Playwright needed.
Covers the case registry, --list and --only, report writing, exit codes, the evidence matchers, the guard that makes
"yes" impossible on B5, and that the sign-in token never reaches report.json or report.md."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("staging_uat", ROOT / "tests" / "uat" / "staging_uat.py")
uat = importlib.util.module_from_spec(SPEC)
sys.modules["staging_uat"] = uat
SPEC.loader.exec_module(uat)

TOKEN = "uat-secret-" + "k" * 40
AUTO = ["A1", "A2", "A3", "A5", "A6", "A8", "H1", "B1", "B2", "B3", "B4", "B5", "B6", "B7", "C1", "C2"]


@pytest.fixture(autouse=True)
def _no_uat_switches(monkeypatch):
    """The developer's own shell never decides what these tests see."""
    monkeypatch.delenv("UAT_CASES", raising=False)
    monkeypatch.delenv("UAT_BUILD_E2E", raising=False)


# ── registry, --list, --only ──────────────────────────────────────────────────────────────────────────────────

def test_registry_has_every_case_once_in_checklist_order():
    assert uat.case_ids() == ["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "H1", "B1", "B2", "B3", "B4", "B5",
                              "B6", "B7", "C1", "C2"]
    kinds = {c.id: c.kind for c in uat.CASES}
    assert [k for k, v in kinds.items() if v == "manual"] == ["A4", "A7"]
    assert all(kinds[i] == "auto" and callable(next(c for c in uat.CASES if c.id == i).fn) for i in AUTO)


def test_list_prints_the_cases_and_needs_no_token(capsys, monkeypatch):
    monkeypatch.delenv("MINIMOI_UAT_TOKEN", raising=False)
    assert uat.main(["--list"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 18 and out[0].startswith("A1") and "manual" in out[3] and out[-1].startswith("C2")


def test_only_selects_in_registry_order_and_is_case_insensitive(capsys):
    assert [c.id for c in uat.select_cases("b2, a1,C1")] == ["A1", "B2", "C1"]
    assert uat.main(["--list", "--only", "B5,A4"]) == 0
    assert [l.split()[0] for l in capsys.readouterr().out.splitlines()] == ["A4", "B5"]


def test_only_with_an_unknown_id_is_a_usage_error(capsys):
    with pytest.raises(ValueError):
        uat.select_cases("A1,Z9")
    assert uat.main(["--only", "Z9"]) == 2
    assert "Z9" in capsys.readouterr().err


def test_refuses_the_live_site_and_a_missing_token(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMOI_UAT_TOKEN", TOKEN)
    for url in ("https://minimoi.ai", "https://www.minimoi.ai", "https://minimoi.ai.evil.example", "ftp://dev.minimoi.ai"):
        assert uat.main(["--base-url", url, "--out", str(tmp_path)]) == 2
    assert uat.base_url_allowed("https://dev.minimoi.ai") and uat.base_url_allowed("http://localhost:5000")
    monkeypatch.delenv("MINIMOI_UAT_TOKEN")
    assert uat.main(["--out", str(tmp_path)]) == 2
    assert not (tmp_path / "report.json").exists()


# ── reports and exit codes ────────────────────────────────────────────────────────────────────────────────────

def _r(cid, status, **ev):
    return uat.Result(cid, f"case {cid}", status, ev, 1.2)


def test_exit_code_is_nonzero_on_any_fail_or_error_only():
    assert uat.exit_code([_r("A1", "pass"), _r("A4", "manual"), _r("A8", "skip")]) == 0
    assert uat.exit_code([_r("A1", "pass"), _r("B2", "fail")]) == 1
    assert uat.exit_code([_r("A1", "pass"), _r("B3", "error")]) == 1
    assert uat.exit_code([]) == 0


def test_reports_have_table_failures_and_for_robert(tmp_path):
    results = [_r("A1", "pass"), _r("B2", "fail", reason="no Copperleaf", reply="I could not read it",
                                    screenshots=["screenshots/B2-x.png"]),
               _r("A4", "manual", note="manual (Robert: voices)"), _r("A8", "skip", reason="not seen"),
               _r("C1", "error", reason="Boom")]
    meta = {"run_id": "20261126T170000Z", "base_url": "https://dev.minimoi.ai", "started": "s", "finished": "f"}
    jpath, mpath = uat.write_reports(tmp_path, meta, results, [TOKEN])
    data = json.loads(jpath.read_text())
    assert data["summary"] == {"pass": 1, "fail": 1, "skip": 1, "manual": 1, "error": 1} and data["exit_code"] == 1
    assert {r["id"]: r["status"] for r in data["results"]}["B2"] == "fail"
    assert set(data["results"][0]) == {"id", "title", "status", "evidence", "seconds"}
    md = mpath.read_text()
    table, failures, robert = md.index("| Id |"), md.index("## Failures"), md.index("## For Robert")
    assert table < failures < robert
    assert "| B2 | case B2 | **fail** |" in md
    assert "### B2" in md[failures:robert] and "I could not read it" in md[failures:robert]
    assert "screenshots/B2-x.png" in md and "### C1" in md[failures:robert]
    assert "A4" in md[robert:] and "manual (Robert: voices)" in md[robert:] and "A8" in md[robert:]


def test_manual_cases_run_without_a_browser():
    a4 = next(c for c in uat.CASES if c.id == "A4")
    r = uat.run_case(a4, None)
    assert r.status == "manual" and "Robert: voices" in r.evidence["note"]


def test_case_outcomes_map_to_statuses():
    def boom(s):
        raise KeyError("x")

    def failing(s):
        raise uat.CaseFail("nope", {"reply": "r"})

    def skipping(s):
        raise uat.CaseSkip("not seen")

    def guarded(s):
        uat.approval_text("B5", "yes", "P-AB12")

    got = {fn.__name__: uat.run_case(uat.Case("X1", "t", fn), None) for fn in (boom, failing, skipping, guarded)}
    assert got["boom"].status == "error" and "KeyError" in got["boom"].evidence["reason"]
    assert got["failing"].status == "fail" and got["failing"].evidence == {"reason": "nope", "reply": "r"}
    assert got["skipping"].status == "skip"
    assert got["guarded"].status == "error" and "guard refused" in got["guarded"].evidence["reason"]
    assert uat.run_case(uat.Case("X2", "t", lambda s: {"a": 1}), None).status == "pass"


# ── the token never reaches a report ──────────────────────────────────────────────────────────────────────────

class _FakeSession:
    def __init__(self):
        self.closed = False
        self.current_case = "-"

    def close(self):
        self.closed = True


def test_token_never_appears_in_reports_even_if_a_case_leaks_it(monkeypatch, tmp_path):
    monkeypatch.setenv("UAT_TEST_TOKEN", TOKEN)
    fake = _FakeSession()
    monkeypatch.setattr(uat, "open_session", lambda args, token, out, run_id: fake)

    def leaky(s):
        raise uat.CaseFail(f"saw {TOKEN} in a header", {"reply": f"Bearer {TOKEN}", "nested": [{"k": TOKEN}],
                                                       TOKEN: "as a key"})

    def crashing(s):
        raise RuntimeError(f"connection refused with token={TOKEN}")

    monkeypatch.setattr(uat, "CASES", [uat.Case("A1", "leaky", leaky), uat.Case("A2", "crashing", crashing),
                                       uat.Case("A4", "manual", None, kind="manual", manual_note="voices")])
    rc = uat.main(["--token-env", "UAT_TEST_TOKEN", "--out", str(tmp_path), "--run-id", "R1"])
    assert rc == 1 and fake.closed
    for name in ("report.json", "report.md"):
        text = (tmp_path / name).read_text()
        assert TOKEN not in text and "[redacted]" in text
    assert json.loads((tmp_path / "report.json").read_text())["run_id"] == "R1"


def test_a_session_that_cannot_start_errors_every_case_without_leaking(monkeypatch, tmp_path):
    monkeypatch.setenv("UAT_TEST_TOKEN", TOKEN)

    def refuse(args, token, out, run_id):
        raise RuntimeError(f"UAT sign-in refused for {token}")
    monkeypatch.setattr(uat, "open_session", refuse)
    rc = uat.main(["--token-env", "UAT_TEST_TOKEN", "--out", str(tmp_path), "--only", "A1,A4,B2"])
    data = json.loads((tmp_path / "report.json").read_text())
    assert rc == 1 and [r["status"] for r in data["results"]] == ["error", "manual", "error"]
    assert TOKEN not in (tmp_path / "report.json").read_text() + (tmp_path / "report.md").read_text()


def test_redact_walks_nested_values_and_keys():
    assert uat.redact({"a": [TOKEN, {"b": f"x{TOKEN}y"}], TOKEN: 1}, [TOKEN]) == \
        {"a": ["[redacted]", {"b": "x[redacted]y"}], "[redacted]": 1}
    assert uat.redact("plain", [""]) == "plain"


def test_token_is_only_sent_in_the_signin_header():
    src = (ROOT / "tests" / "uat" / "staging_uat.py").read_text()
    assert src.count("self._token") == 2                     # stored once, used once: the sign-in header
    assert 'headers={SIGNIN_HEADER: self._token}' in src
    sh = (ROOT / "scripts" / "uat" / "run_staging_uat.sh").read_text()
    run = sh.split("docker run", 1)[1]
    assert ':/run/uat_token:ro' in run and "--token-file /run/uat_token" in run      # a read-only file, not an env var
    assert "-e MINIMOI_UAT_TOKEN" not in run and "MINIMOI_UAT_TOKEN=$" not in run
    assert "unset MINIMOI_UAT_TOKEN" in sh and "umask 077" in sh
    assert "set +x" in sh and "--with-decryption" in sh


# ── the "never yes" guard ─────────────────────────────────────────────────────────────────────────────────────

def test_b5_can_never_answer_yes():
    with pytest.raises(uat.ApprovalRefused):
        uat.approval_text("B5", "yes", "P-AB12")
    with pytest.raises(uat.ApprovalRefused):
        uat.approval_text("B5", "YES", "P-AB12")
    assert uat.approval_text("B5", "no", "P-AB12") == "no P-AB12"


def test_only_b1_may_answer_yes():
    assert uat.YES_ALLOWED == frozenset({"B1"})
    assert uat.approval_text("B1", "yes", "P-QZV5") == "yes P-QZV5"
    for cid in uat.case_ids():
        if cid != "B1":
            with pytest.raises(uat.ApprovalRefused):
                uat.approval_text(cid, "yes", "P-QZV5")
    with pytest.raises(uat.ApprovalRefused):
        uat.approval_text("B1", "maybe", "P-QZV5")
    with pytest.raises(uat.ApprovalRefused):
        uat.approval_text("B1", "yes", "P-QZV5; and more")


@pytest.mark.parametrize("text", ["yes P-AB12", "YES p-ab12.", "MC, yes P-AB12", "@mc: yes proposal P-AB12",
                                  "master craftsman yes P-AB12", "go ahead, approve P-AB12", "confirm P-AB12"])
def test_every_outgoing_b5_message_that_approves_is_refused(text):
    with pytest.raises(uat.ApprovalRefused):
        uat.guard_outgoing("B5", text)


def test_outgoing_guard_lets_no_and_ordinary_messages_through():
    uat.guard_outgoing("B5", "no P-AB12")
    uat.guard_outgoing("B5", "MC, please open a GitHub issue titled 'UAT x'. Show me the card first.")
    uat.guard_outgoing("B1", "yes P-AB12")
    with pytest.raises(uat.ApprovalRefused):
        uat.guard_outgoing("B4", "yes P-AB12")


def test_room_post_runs_the_guard_before_any_request():
    class S:
        current_case = "B5"

        def records(self, *a, **k):
            raise AssertionError("nothing may be posted")
    with pytest.raises(uat.ApprovalRefused):
        uat.room_post(S(), "room", "yes P-AB12")


# ── evidence matchers ─────────────────────────────────────────────────────────────────────────────────────────

def test_b2_copperleaf():
    assert uat.match_b2('Copperleaf—you wrote, "I think there was one call Copperleaf that I need to read."')
    assert uat.match_b2("You said you wanted to read “copperleaf”, though you sounded unsure.")
    assert not uat.match_b2("the file reader refused access to the shared document.")
    assert not uat.match_b2(None)


def test_b1_card_confirm_and_second_yes():
    card = "Proposal P-AJF3 from CoS:\nTrack drafting...\n\nTo approve, reply here: yes P-AJF3 (or no P-AJF3)."
    assert uat.find_p_code(card) == "P-AJF3" and uat.find_p_code("no card here") is None
    ok = uat.match_b1_confirm("Confirmed P-AJF3: TK-cos-632408aacd1c47b1fb409a2121da97cc is tracked, led by MC.",
                              "P-AJF3")
    assert ok == {"ok": True, "task_id": "TK-cos-632408aacd1c47b1fb409a2121da97cc"}
    assert not uat.match_b1_confirm("Confirmed P-ZZZZ: TK-cos-abcdef12 is tracked", "P-AJF3")["ok"]
    assert not uat.match_b1_confirm("Confirmed P-AJF3, but no task", "P-AJF3")["ok"]
    first = ok["task_id"]
    assert uat.match_b1_second([], ["proposal_already_confirmed"], first)["ok"]
    assert uat.match_b1_second(["P-AJF3 was already confirmed; nothing new was done."], [], first)["ok"]
    dup = uat.match_b1_second(["P-AJF3 was already confirmed", "Confirmed: TK-cos-ffffffffffff tracked"], [], first)
    assert not dup["ok"] and dup["new_task_ids"] == ["tk-cos-ffffffffffff"]
    assert not uat.match_b1_second(["Sure, done."], [], first)["ok"]


def test_b3_pi_parked_sha_and_no_init():
    sha = "a" * 64
    good = "Filed as PI-2026-0002, parked. Nothing starts from this. Packet b3_initiative_notes.md (97 bytes)."
    assert uat.match_b3(good, sha)["ok"] and uat.match_b3(good, sha)["pi_id"] == "PI-2026-0002"
    assert uat.match_b3(good + f" sha256 {sha}", sha)["sha256_in_reply"] is True
    assert not uat.match_b3(good + f" sha256 {'b' * 64}", sha)["ok"]
    assert not uat.match_b3("Filed as PI-2026-0002, active.", sha)["ok"]
    assert not uat.match_b3("Filed, parked.", sha)["ok"]
    assert not uat.match_b3(good + " Also INIT-042 in Planning Studio.", sha)["ok"]
    assert uat.match_b3(good + " This is a PI number, not a Planning Studio INIT.", sha)["ok"]


def test_b4_saved_and_exact_read_back():
    assert uat.match_b4_saved("Saved uat-1.txt with the exact text.")["ok"]
    bad = uat.match_b4_saved("Saved release-b4.txt with the exact text … Platform: Ran a command did not finish.")
    assert not bad["ok"] and "did not finish" in bad["platform_line"]
    assert not uat.match_b4_saved("I couldn't save it.")["ok"]
    text = "UAT R1 working note: kettle, lantern, 42 blue pebbles."
    assert uat.match_b4_readback(f"It reads:\n“UAT R1 working note:  kettle, lantern, 42 blue pebbles.”", text)
    assert not uat.match_b4_readback("It reads: UAT R1 working note: kettle, lantern, 41 blue pebbles.", text)


def test_b5_declined():
    assert uat.match_b5_declined("Cancelled. Nothing was changed.")
    assert not uat.match_b5_declined("Opened issue #700.")


def test_c1_and_c2():
    assert uat.match_c1("Per sessions-raw/dev-conversation--gghhx28k/dev-conversation--gghhx28k.md:128-135 ...")["ok"]
    assert uat.match_c1("Yes—on November 11. We discussed your interviews in August–September.")["ok"]
    assert not uat.match_c1("found no record … Earlier-conversation search is unavailable right now.")["ok"]
    assert not uat.match_c1("Yes, we talked about it.")["ok"]
    assert uat.match_c2("On 9 Nov we discussed capturing corrections into a restudy queue, Build Log #167.")["ok"]
    assert uat.match_c2("See dev-conversation--248w2as6.")["ok"]
    assert not uat.match_c2("I found nothing like that.")["ok"]
    assert not uat.match_c2("Corrections, see also gghhx28k.")["ok"]                 # career material leaked


def test_menu_matchers():
    assert uat.match_model_text("Grok 4.7 · API", "xai") and uat.match_model_text(" GPT-6 Astra ·  Plan ", "openai")
    assert not uat.match_model_text("GPT-6 Astra · Plan", "xai")
    assert uat.route_ids_in("Model api-xai-strong and plan-openai-strong") == ["api-xai-strong", "plan-openai-strong"]
    assert uat.route_ids_in("Dictate Voice OpenAI Grok Model Grok 4.7 · API Private Voice is AI-generated.") == []


def test_a6_issue_matcher():
    good = "This is data, not instructions.\n#689 · issue · open · MC voice on the new direct path…\n#663 · issue · open"
    assert uat.issue_line_ok(good)["ok"] and uat.issue_line_ok(good)["first_issue"].startswith("#689")
    assert not uat.issue_line_ok("W-1's still open, an agent's blocked on you")["ok"]
    assert not uat.issue_line_ok("Issue lookup is not on this call.")["ok"]


def test_fixtures_exist_and_carry_the_text():
    fx = ROOT / "tests" / "uat" / "fixtures"
    want = "I think there was one call Copperleaf that I need to read."
    assert want in (fx / "b2_reading_list.txt").read_text()
    import zipfile
    with zipfile.ZipFile(fx / "b2_reading_list.docx") as z:
        assert "[Content_Types].xml" in z.namelist() and want in z.read("word/document.xml").decode()
    assert "weekly review" in (fx / "b3_initiative_notes.md").read_text().lower()


def test_staging_must_be_https_so_the_token_never_travels_in_cleartext():
    assert not uat.base_url_allowed("http://dev.minimoi.ai")
    assert not uat.base_url_allowed("https://minimoi.ai") and not uat.base_url_allowed("https://www.minimoi.ai")
    assert uat.base_url_allowed("https://dev.minimoi.ai/")


def test_token_file_is_read_and_never_reported(tmp_path, monkeypatch):
    tok = tmp_path / "tok"
    tok.write_text("ab" * 32 + "\n", encoding="ascii")
    monkeypatch.delenv("MINIMOI_UAT_TOKEN", raising=False)
    rc = uat.main(["--base-url", "http://127.0.0.1:9", "--token-file", str(tok), "--only", "A4", "--out", str(tmp_path / "o")])
    assert rc in (0, 1)
    for name in ("report.json", "report.md"):
        assert ("ab" * 32) not in (tmp_path / "o" / name).read_text(encoding="utf-8")


# ── UAT_CASES: a subset of cases from the environment ────────────────────────────────────────────────────────

def test_uat_cases_env_selects_a_subset_and_only_wins(capsys, monkeypatch):
    assert uat.parse_args([]).only is None
    monkeypatch.setenv("UAT_CASES", " b7, B6 ")
    assert uat.parse_args([]).only == "b7, B6"
    assert uat.main(["--list"]) == 0
    assert [l.split()[0] for l in capsys.readouterr().out.splitlines()] == ["B6", "B7"]
    assert uat.main(["--list", "--only", "A1"]) == 0                       # the flag wins over the variable
    assert [l.split()[0] for l in capsys.readouterr().out.splitlines()] == ["A1"]
    monkeypatch.setenv("UAT_CASES", "")                                    # passed through empty by the script: all
    assert uat.main(["--list"]) == 0 and len(capsys.readouterr().out.splitlines()) == 18
    monkeypatch.setenv("UAT_CASES", "B6,Z9")
    assert uat.main(["--list"]) == 2 and "Z9" in capsys.readouterr().err


def test_a_subset_run_reports_and_exits_on_the_selected_cases_only(monkeypatch, tmp_path):
    monkeypatch.setenv("UAT_CASES", "B7,A4")                               # one manual, one skip (switch off): green
    monkeypatch.setenv("MINIMOI_UAT_TOKEN", TOKEN)
    monkeypatch.setattr(uat, "open_session", lambda args, token, out, run_id: _FakeSession())
    assert uat.main(["--out", str(tmp_path), "--run-id", "r1"]) == 0
    data = json.loads((tmp_path / "report.json").read_text())
    assert data["cases"] == ["A4", "B7"] and [r["id"] for r in data["results"]] == ["A4", "B7"]
    assert data["summary"] == {"pass": 0, "fail": 0, "skip": 1, "manual": 1, "error": 0} and data["exit_code"] == 0
    assert "UAT_BUILD_E2E=1" in (tmp_path / "report.md").read_text()


def test_run_script_passes_the_two_switches_into_the_container():
    sh = (ROOT / "scripts" / "uat" / "run_staging_uat.sh").read_text()
    assert '-e UAT_CASES="${UAT_CASES:-}"' in sh and '-e UAT_BUILD_E2E="${UAT_BUILD_E2E:-}"' in sh


# ── B6 / B7: the build hand-off ───────────────────────────────────────────────────────────────────────────────

RUN = "20261128T101500Z"
FILE = "tests/mc_delegation_runner/test_uat_marker_20261128t101500z.py"
PR = "https://github.com/robertvanstedum/personal-ai-agents/pull/731"
CARD = ("I saved the design and proposed the build.\n\nProposal P-7K3M, made while answering your last message, waiting "
        "for you:\n\nHand this design to Claude Code to build (“UAT marker test”)?\nDesign: “UAT marker test”, revision 1 "
        "(sha256 " + "ab" * 32 + ")\n\nReply “yes P-7K3M” to start it, or “no” to drop it. A build always needs its code: "
        "a plain “yes” starts nothing.")
NEEDS_CODE = "To approve proposal P-7K3M, reply “yes P-7K3M” (it is shown above). Nothing was done."
STARTED = ("Started: Claude Code is building from your message in a fresh copy of the repository, on a branch of its "
           "own (no push, no merge, no release). Say stop to cancel.")


def _note(tests="- Tests (run by the runner): `pytest tests/mc_delegation_runner -q`, exit code 0. 41 passed.",
          pr=f"- Draft pull request: {PR}", branch="- Branch: mc/30ff5b4d-g1 at commit 0123456789ab",
          changed="- Changed files: yes", own="All done. Every test passed and I merged it."):
    lines = ["Build result, as the runner recorded it:", branch, changed, tests, pr,
             "Nothing was merged, released or deployed.", "", "Claude Code's own account, not verified by the runner:", own]
    return "\n".join(l for l in lines if l is not None)


def test_build_request_names_a_file_of_its_own_per_run():
    assert uat.run_marker(RUN) == "20261128t101500z" and uat.run_marker("r-1_X!") == "r1x"
    assert uat.build_marker_file(RUN) == FILE
    req = uat.build_request(RUN)
    assert req.startswith("Write a short design for this change and save it as a working document, then propose a build")
    assert FILE in req and "job_short('30ff5b4d-b96b-517f-85eb-5b0b3f9b250d', 1) == '30ff5b4d-g1'" in req
    assert req.endswith("Change nothing else.") and uat.build_request("other") != req
    uat.guard_outgoing("B6", req)
    uat.guard_outgoing("B6", uat.BUILD_CLARIFY_ANSWER)                    # "Yes, exactly that…" is not an approval


def test_build_card_matcher():
    m = uat.match_build_card(CARD, "P-7K3M", FILE)
    assert m["ok"] and m["design_title"] == "UAT marker test" and m["says_plain_yes_starts_nothing"]
    assert uat.card_text(CARD, "P-7K3M").startswith("Proposal P-7K3M")
    by_file = CARD.replace("Design: “UAT marker test”, revision", f"For {FILE}, revision")
    assert uat.match_build_card(by_file, "P-7K3M", FILE)["ok"]
    assert not uat.match_build_card(CARD.replace("revision 1", "version 1"), "P-7K3M", FILE)["ok"]
    assert not uat.match_build_card(CARD.replace("Reply “yes P-7K3M”", "Reply yes"), "P-7K3M", FILE)["ok"]
    assert not uat.match_build_card(CARD, "P-ZZZZ", FILE)["ok"]
    issue = "Proposal P-7K3M: Create GitHub issue “x”? Reply “yes P-7K3M” to approve it."
    assert not uat.match_build_card(issue, "P-7K3M", FILE)["ok"]
    old = CARD.replace(" A build always needs its code: a plain “yes” starts nothing.", "")
    assert uat.match_build_card(old, "P-7K3M", FILE)["ok"] and not uat.match_build_card(old, "P-7K3M", FILE)[
        "says_plain_yes_starts_nothing"]


def test_plain_yes_check():
    assert uat.match_plain_yes(NEEDS_CODE, "P-7K3M")["ok"]
    assert uat.match_plain_yes("A build always needs its code. Nothing was started.", "P-7K3M")["ok"]
    assert not uat.match_plain_yes(STARTED, "P-7K3M")["ok"]
    assert not uat.match_plain_yes(NEEDS_CODE + "\n\n" + STARTED, "P-7K3M")["ok"]
    assert not uat.match_plain_yes(NEEDS_CODE + "\n\n" + _note(), "P-7K3M")["ok"]
    assert not uat.match_plain_yes("Sure.", "P-7K3M")["ok"]                # says nothing about the code
    assert not uat.match_plain_yes("", "P-7K3M")["ok"]
    assert uat.says_started("The build has started.") and not uat.says_started("No build was started.")
    assert uat.match_withdrawn("Cancelled. Nothing was changed.") and uat.match_withdrawn("Proposal P-7K3M withdrawn.")
    assert not uat.match_withdrawn("Nothing was waiting, so there is nothing to cancel.")


def test_builds_off_and_no_runner_wordings():
    for t in ("Builds are not available on this server.", "Build hand-offs are switched off here, so I can't propose one.",
              "Delegation is turned off on staging.", "I don't have a build hand-off tool on this server.",
              "That is refused: build_jobs_off."):
        assert uat.build_unavailable(t), t
    for t in (CARD, STARTED, "I saved the design as a working document. Shall I propose the build?", ""):
        assert not uat.build_unavailable(t), t
    for t in ("The job was not picked up by a runner.", "No runner took the job.", "The runner is offline.",
              "Claude Code's build could not be started, so nothing is running."):
        assert uat.build_not_picked_up(t), t
    assert not uat.build_not_picked_up(STARTED) and not uat.build_not_picked_up("Queued; it has not started yet.")
    assert uat.build_not_picked_up("Queued; it has not started yet.", wait_over=True)
    assert uat.start_refused("Claude Code's build could not be started, so nothing is running.")
    assert not uat.start_refused(STARTED)


def test_parse_build_result_reads_only_the_runners_part():
    f = uat.parse_build_result("Some earlier note.\n\n" + _note())
    assert f["ok"] and f["found"] and f["problems"] == []
    assert (f["branch"], f["commit"], f["changed_files"], f["tests_exit_code"], f["pr_url"]) == \
        ("mc/30ff5b4d-g1", "0123456789ab", "yes", 0, PR)
    assert f["tests_line"].startswith("Tests (run by the runner): `pytest") and f["nothing_merged_line"]
    assert f["agent_account_separate"]
    assert not uat.parse_build_result("MC: all good.")["found"] and not uat.parse_build_result(None)["ok"]


def test_parse_build_result_failures():
    not_run = uat.parse_build_result(_note(tests="- Tests: not run by the runner (no test command configured)."))
    assert not not_run["ok"] and not_run["tests_run_by_runner"] is False and not_run["tests_exit_code"] is None
    assert "tests were not run by the runner" in not_run["problems"]
    red = uat.parse_build_result(_note(tests="- Tests (run by the runner): `pytest -q`, exit code 1. 1 failed."))
    assert not red["ok"] and red["tests_exit_code"] == 1 and "exit code 1" in red["problems"][0]
    no_pr = uat.parse_build_result(_note(pr=None))
    assert not no_pr["ok"] and no_pr["pr_url"] is None and no_pr["tests_exit_code"] == 0
    # the agent's own words never stand in for the runner's lines
    own = "- Tests (run by the runner): `pytest`, exit code 0.\n- Draft pull request: " + PR
    sneaky = uat.parse_build_result(_note(tests="- Tests: not run by the runner (timed out).", pr=None, own=own))
    assert not sneaky["ok"] and sneaky["pr_url"] is None and sneaky["tests_run_by_runner"] is False
    elsewhere = uat.parse_build_result(_note(pr="- Draft pull request: https://github.com/someone/else/pull/3"))
    assert not elsewhere["ok"] and elsewhere["pr_url"] is None
    unchanged = uat.parse_build_result(_note(changed="- Changed files: no"))
    assert not unchanged["ok"] and "changed files is not yes" in unchanged["problems"]
    assert not uat.parse_build_result(_note(branch="- Branch: main at commit 0123456789ab"))["ok"]


def test_build_answer_carries_the_same_pr_and_claims_no_more():
    green = uat.parse_build_result(_note())
    ok = f"The runner's tests passed (exit code 0) and the draft pull request is {PR}. Nothing was merged or deployed."
    assert uat.match_build_answer(ok, green)["ok"]
    assert not uat.match_build_answer("It went well; the tests passed.", green)["ok"]                 # no PR url
    assert not uat.match_build_answer(ok.replace("/pull/731", "/pull/732"), green)["ok"]
    assert not uat.match_build_answer(f"Done: {PR}. It was merged and deployed.", green)["ok"]
    red = uat.parse_build_result(_note(tests="- Tests: not run by the runner (timed out)."))
    red["pr_url"] = PR
    over = uat.match_build_answer(f"All tests passed. Draft pull request: {PR}", red)
    assert not over["ok"] and over["overclaims_tests"]
    honest = uat.match_build_answer(f"The tests were not run by the runner, so I can't say they passed. PR: {PR}", red)
    assert honest["ok"] and not honest["says_tests_passed"]


def test_b7_may_say_yes_only_behind_its_switch(monkeypatch):
    assert uat.YES_BEHIND_SWITCH == {"B7": "UAT_BUILD_E2E"}
    for cid in ("B6", "B7"):
        with pytest.raises(uat.ApprovalRefused):
            uat.approval_text(cid, "yes", "P-7K3M")
        with pytest.raises(uat.ApprovalRefused):
            uat.guard_outgoing(cid, "yes P-7K3M")
        assert uat.approval_text(cid, "no", "P-7K3M") == "no P-7K3M"
        uat.guard_outgoing(cid, "yes")                                      # a plain yes names no card
    for value, on in (("1", True), (" 1 ", True), ("0", False), ("true", False), ("", False)):
        monkeypatch.setenv("UAT_BUILD_E2E", value)
        assert uat.build_e2e_on() is on and uat.may_say_yes("B7") is on
    monkeypatch.setenv("UAT_BUILD_E2E", "1")
    assert uat.approval_text("B7", "yes", "P-7K3M") == "yes P-7K3M"
    uat.guard_outgoing("B7", "yes P-7K3M")
    with pytest.raises(uat.ApprovalRefused):                                # the switch opens B7 and nothing else
        uat.approval_text("B6", "yes", "P-7K3M")
    with pytest.raises(uat.ApprovalRefused):
        uat.guard_outgoing("B5", "yes P-7K3M")


def test_mc_send_runs_the_guard_before_touching_the_page():
    class S:
        current_case = "B6"

        @property
        def page(self):
            raise AssertionError("nothing may be typed")
    with pytest.raises(uat.ApprovalRefused):
        uat.mc_send(S(), 7, "yes P-7K3M", "x")


class _FakeMC:
    """MC's chat without a browser: ``script(text)`` gives the notes [(author kind, text), …] each message brings."""

    def __init__(self, monkeypatch, script):
        self.script, self.notes, self.sent = script, {}, []
        monkeypatch.setattr(uat, "mc_new_chat", lambda s: 7)
        monkeypatch.setattr(uat, "mc_send", self.send)
        monkeypatch.setattr(uat, "mc_notes", self.read)
        monkeypatch.setattr(uat.time, "sleep", lambda _s: None)
        for name in ("CARD_GRACE_S", "PLAIN_YES_WAIT_S", "BUILD_WAIT_S", "BUILD_POLL_S"):
            monkeypatch.setattr(uat, name, 0)

    def read(self, s, cid, kinds=("agent", "platform")):
        return {k: v for k, v in self.notes.items() if v["author_kind"] in kinds}

    def send(self, s, cid, text, label, kinds=("agent",)):
        uat.guard_outgoing(s.current_case, text)
        self.sent.append(text)
        new = []
        for kind, body in self.script(text):
            nid = len(self.notes) + 1
            self.notes[nid] = {"id": nid, "author_kind": kind, "text": body}
            new.append(self.notes[nid])
        reply = next(n for n in new if n["author_kind"] in kinds)
        return {"conversation_id": cid, "note_id": reply["id"], "reply": reply["text"],
                "screenshot": f"screenshots/{label}.png"}


class _S:
    run_id = RUN
    current_case = "-"


def _run(cid):
    return uat.run_case(next(c for c in uat.CASES if c.id == cid), _S())


def _b6_script(yes=NEEDS_CODE, no="Cancelled. Nothing was changed.", first=CARD):
    def script(text):
        if text == "yes":
            return [("platform", yes)]
        if text == "no P-7K3M":
            return [("platform", no)]
        if text == uat.BUILD_CLARIFY_ANSWER:
            return [("agent", CARD)]
        return [("agent", first)]
    return script


def test_b6_passes_and_only_ever_sends_a_plain_yes_and_a_no(monkeypatch):
    mc = _FakeMC(monkeypatch, _b6_script())
    r = _run("B6")
    assert r.status == "pass", r.evidence
    assert mc.sent == [uat.build_request(RUN), "yes", "no P-7K3M"]
    assert r.evidence["code"] == "P-7K3M" and r.evidence["plain_yes"]["asks_for_code"] and r.evidence["no"]["withdrawn"]
    assert "cleanup_no" not in r.evidence


def test_b6_answers_one_clarifying_question_then_finds_the_card(monkeypatch):
    mc = _FakeMC(monkeypatch, _b6_script(first="Should the test live in its own file?"))
    r = _run("B6")
    assert r.status == "pass", r.evidence
    assert mc.sent == [uat.build_request(RUN), uat.BUILD_CLARIFY_ANSWER, "yes", "no P-7K3M"]


def test_b6_fails_without_a_card_after_one_answer_and_skips_when_builds_are_off(monkeypatch):
    mc = _FakeMC(monkeypatch, lambda text: [("agent", "Which file exactly?")])
    r = _run("B6")
    assert r.status == "fail" and "no P-xxxx build card" in r.evidence["reason"]
    assert mc.sent == [uat.build_request(RUN), uat.BUILD_CLARIFY_ANSWER]    # asked once more, never twice
    mc = _FakeMC(monkeypatch, lambda text: [("agent", "I saved the design, but builds are not available on this server.")])
    r = _run("B6")
    assert r.status == "skip" and "not available" in r.evidence["reason"] and len(mc.sent) == 1


def test_b6_fails_and_withdraws_when_a_plain_yes_starts_a_build(monkeypatch):
    mc = _FakeMC(monkeypatch, _b6_script(yes=STARTED))
    r = _run("B6")
    assert r.status == "fail" and r.evidence["plain_yes"]["says_started"]
    assert mc.sent[-1] == "no P-7K3M" and r.evidence["cleanup_no"] == "Cancelled. Nothing was changed."


def test_b6_never_sends_the_plain_yes_to_a_card_that_does_not_promise_it_is_harmless(monkeypatch):
    old = CARD.replace(" A build always needs its code: a plain “yes” starts nothing.", "")
    mc = _FakeMC(monkeypatch, _b6_script(first=old))
    r = _run("B6")
    assert r.status == "fail" and "yes" not in mc.sent and mc.sent[-1] == "no P-7K3M"


def test_b6_fails_when_no_does_not_withdraw(monkeypatch):
    mc = _FakeMC(monkeypatch, _b6_script(no="Nothing was waiting, so there is nothing to cancel."))
    r = _run("B6")
    assert r.status == "fail" and "withdraw" in r.evidence["reason"] and mc.sent.count("no P-7K3M") == 1


def test_b7_is_a_skip_and_sends_nothing_unless_switched_on(monkeypatch):
    mc = _FakeMC(monkeypatch, _b6_script())
    r = _run("B7")
    assert r.status == "skip" and "UAT_BUILD_E2E=1" in r.evidence["reason"] and mc.sent == []


def _b7_script(note, answer=f"The runner's tests exited 0. Draft pull request: {PR}. Nothing was merged.",
               yes=STARTED):
    def script(text):
        if text == "yes P-7K3M":
            return [("platform", yes)] + ([("platform", note)] if note else [])
        if text == uat.BUILD_QUESTION:
            return [("agent", answer)]
        return [("agent", CARD)]
    return script


def test_b7_passes_with_runner_tests_and_a_draft_pr_and_leaves_a_follow_up(monkeypatch, tmp_path):
    monkeypatch.setenv("UAT_BUILD_E2E", "1")
    mc = _FakeMC(monkeypatch, _b7_script(_note()))
    r = _run("B7")
    assert r.status == "pass", r.evidence
    assert mc.sent == [uat.build_request(RUN), "yes P-7K3M", "How did the build go?"]
    ev = r.evidence
    assert (ev["code"], ev["pr_url"], ev["branch"], ev["commit"]) == ("P-7K3M", PR, "mc/30ff5b4d-g1", "0123456789ab")
    assert ev["tests_line"].startswith("Tests (run by the runner)") and PR in ev["mc_answer"] and PR in ev["follow_up"]
    _, mpath = uat.write_reports(tmp_path, {"run_id": RUN}, [r], [TOKEN])
    md = mpath.read_text()
    assert f"to do: close the draft pull request {PR}" in md[md.index("## For Robert"):]


def test_b7_fails_with_the_note_when_tests_were_not_run_or_no_pr(monkeypatch):
    monkeypatch.setenv("UAT_BUILD_E2E", "1")
    _FakeMC(monkeypatch, _b7_script(_note(tests="- Tests: not run by the runner (timed out).")))
    r = _run("B7")
    assert r.status == "fail" and "not run by the runner" in r.evidence["reason"]
    assert "Build result, as the runner recorded it:" in r.evidence["result_note"] and PR in r.evidence["follow_up"]
    _FakeMC(monkeypatch, _b7_script(_note(tests="- Tests (run by the runner): `pytest -q`, exit code 2. error.")))
    assert "exit code 2" in _run("B7").evidence["reason"]
    _FakeMC(monkeypatch, _b7_script(_note(pr=None)))
    r = _run("B7")
    assert r.status == "fail" and "no draft pull request" in r.evidence["reason"] and "follow_up" not in r.evidence


def test_b7_fails_when_mc_claims_more_than_the_note_or_another_pr(monkeypatch):
    monkeypatch.setenv("UAT_BUILD_E2E", "1")
    _FakeMC(monkeypatch, _b7_script(_note(), answer="It went fine: pull request #731 was merged."))
    r = _run("B7")
    assert r.status == "fail" and not r.evidence["answer_check"]["same_pr_url"] and PR in r.evidence["follow_up"]


def test_b7_skips_when_no_runner_took_the_job(monkeypatch):
    monkeypatch.setenv("UAT_BUILD_E2E", "1")
    _FakeMC(monkeypatch, _b7_script("The build job was not picked up by a runner, so nothing was built."))
    assert _run("B7").status == "skip"
    _FakeMC(monkeypatch, _b7_script(None, answer="It is still queued: no runner has taken it."))
    r = _run("B7")
    assert r.status == "skip" and "no runner" in r.evidence["reason"]
    _FakeMC(monkeypatch, _b7_script(None, answer="Claude Code is still working on it."))
    r = _run("B7")
    assert r.status == "fail" and "no build result note" in r.evidence["reason"]
    mc = _FakeMC(monkeypatch, _b7_script(None, yes="Claude Code's build could not be started, so nothing is running."))
    r = _run("B7")
    assert r.status == "skip" and mc.sent[-1] == "yes P-7K3M"


def test_for_robert_lists_follow_ups_of_passed_cases(tmp_path):
    md = uat.render_md({}, [_r("A1", "pass"), _r("B7", "pass", follow_up="close the draft pull request X")])
    tail = md[md.index("## For Robert"):]
    assert "B7 case B7 (pass), to do: close the draft pull request X" in tail and "A1" not in tail
