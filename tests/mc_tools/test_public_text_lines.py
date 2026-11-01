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


def test_public_punctuation_is_allowed_but_lookalike_letters_are_not(monkeypatch):
    check = _filter(monkeypatch)
    assert check('CoS → Guild — “ready”; ‘confirmed’ … • next ← previous ↔ both') is None
    assert check('Аna is ready') == "non_ascii_in_public_text"  # Cyrillic A
    assert check('An\u200ba is ready') == "non_ascii_in_public_text"


def test_login_email_domains_are_not_guest_names(monkeypatch):
    _filter(monkeypatch)
    svc = object.__new__(service.ToolService)
    svc.people = lambda: ["person@example.com person@example.com", "Synthetic Reader reader@gmail.com"]
    check = lambda text: svc._public_text_refusal(None, text)
    assert check("See https://github.com/example/product; gmail delivery is working.") is None
    assert check("The person asked for access") == "name_in_public_text"
    assert check("The reader asked for access") == "name_in_public_text"
    assert check("P e r s o n asked for access") == "name_in_public_text"
    assert check("The synthetic account") == "name_in_public_text"
    assert check("person@example.com") == "email_in_public_text"


def test_supported_punctuation_does_not_hide_guest_names(monkeypatch):
    _filter(monkeypatch)
    svc = object.__new__(service.ToolService)
    svc.people = lambda: ["Bruno bruno@example.com"]
    assert svc._public_text_refusal(None, "Bruno’s request") == "name_in_public_text"
    for separator in ["—", "–", "→", "←", "↔", "•", "‘", "’", "“", "”", "…"]:
        assert svc._public_text_refusal(None, separator.join("Bruno")) == "name_in_public_text"
