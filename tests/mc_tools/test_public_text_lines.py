"""The public-repository text filter (#395, 27 Oct): paragraphs pass, and a refusal names the rule it hit."""
import contextlib

import core.mc_tools.service as service


class _NoGuests:
    def execute(self, *a):
        pass

    def fetchall(self):
        return []


def _filter(monkeypatch):
    monkeypatch.setattr(service, "_cursor", lambda conn: contextlib.nullcontext(_NoGuests()))
    svc = object.__new__(service.ToolService)
    svc.people = None
    return lambda text: svc._public_text_refusal(None, text)


def test_an_issue_body_in_paragraphs_with_issue_numbers_passes(monkeypatch):
    check = _filter(monkeypatch)
    body = ("Since #408, notes reach MC as written.\n\n\tDone when Send passes the text through.\n\n"
            "Related: #406, #408. Milestone: MiniMoi 1.2.")
    assert check(body) is None


def test_other_control_characters_and_lookalikes_still_refuse(monkeypatch):
    check = _filter(monkeypatch)
    assert check("A carriage\rreturn") == "non_ascii_in_public_text"
    assert check("A résumé paste") == "non_ascii_in_public_text"
    assert check("Mail me at a@b.test") == "email_in_public_text"


def test_a_refusal_says_which_rule_it_hit():
    r = service._public_text_refused("non_ascii_in_public_text")
    assert r["status"] == "refused" and r["code"] == "non_ascii_in_public_text"
    assert "outside plain ASCII" in r["message"] and "line breaks" in r["message"]
