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
AUTO = ["A1", "A2", "A3", "A5", "A6", "A8", "H1", "B1", "B2", "B3", "B4", "B5", "C1", "C2"]


# ── registry, --list, --only ──────────────────────────────────────────────────────────────────────────────────

def test_registry_has_every_case_once_in_checklist_order():
    assert uat.case_ids() == ["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "H1", "B1", "B2", "B3", "B4", "B5",
                              "C1", "C2"]
    kinds = {c.id: c.kind for c in uat.CASES}
    assert [k for k, v in kinds.items() if v == "manual"] == ["A4", "A7"]
    assert all(kinds[i] == "auto" and callable(next(c for c in uat.CASES if c.id == i).fn) for i in AUTO)


def test_list_prints_the_cases_and_needs_no_token(capsys, monkeypatch):
    monkeypatch.delenv("MINIMOI_UAT_TOKEN", raising=False)
    assert uat.main(["--list"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 16 and out[0].startswith("A1") and "manual" in out[3] and out[-1].startswith("C2")


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
    assert "-e MINIMOI_UAT_TOKEN \\" in sh and "MINIMOI_UAT_TOKEN=$" not in sh.split("docker run", 1)[1]
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
