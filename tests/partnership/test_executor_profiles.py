"""B4 v0.5/v0.6: executor profiles and the review verdict contract. Money, worker and route come from config only."""
import json
from pathlib import Path

from core.mc_delegation_dispatcher import cards as card_mod
from services.partnership_tasks import contracts

ROOT = Path(__file__).resolve().parents[2]
PROFILES = json.loads((ROOT / "config/partnership/executor_profiles.json").read_text())


def test_profiles_name_only_card_families_and_compile_as_cards():
    cat = card_mod.Catalogue.load()
    assert set(PROFILES["profiles"]) == {"claude-code", "codex"}               # grok-cli is not a card family (S2)
    for seat, p in PROFILES["profiles"].items():
        assert cat.families[seat] == p["family"]
        for kind, row in (("build", "build"), ("review", "review")):
            w = p[kind]
            doc = {"schema": "minimoi.card.v1", "card_id": f"C-prof-{seat}-{kind}", "class": w["class"],
                   "class_version": cat.classes[w["class"]]["version"], "repository": PROFILES["repository"],
                   "ref": "a" * 40, "workers": {("builder" if kind == "build" else "reviewer"):
                                                {k: w[k] for k in ("actor", "route", "amount", "cost_cap_usd")}},
                   "briefs": {row: "A bounded step."}, "attempts": w["attempts"], "step_deadline_s": w["step_deadline_s"]}
            assert cat.compile(doc).card_id == doc["card_id"]
    assert PROFILES["profiles"]["claude-code"]["family"] != PROFILES["profiles"]["codex"]["family"]


def test_routes_are_the_dev_authoritys_and_the_acceptance_profile_spends_nothing():
    for seat, p in PROFILES["profiles"].items():
        for kind in ("build", "review"):
            assert p[kind]["route"] == f"{seat}.subscription"
    fake = json.loads((ROOT / "config/partnership/executor_profiles.fakework.json").read_text())
    cat = card_mod.Catalogue.load()
    for seat, p in fake["profiles"].items():
        assert p["family"] == PROFILES["profiles"][seat]["family"]
        for kind in ("build", "review"):
            assert p[kind]["actor"] == p[kind]["route"] == "fakework" and float(p[kind]["cost_cap_usd"]) <= 0.01
            assert cat.families[p[kind]["actor"]] == "fakework"
            w = p[kind]
            doc = {"schema": "minimoi.card.v1", "card_id": f"C-fk-{seat}-{kind}", "class": w["class"], "class_version": 1,
                   "repository": "personal-ai-agents", "ref": "a" * 40,
                   "workers": {("builder" if kind == "build" else "reviewer"):
                               {k: w[k] for k in ("actor", "route", "amount", "cost_cap_usd")}},
                   "briefs": {kind: "x"}, "attempts": 1, "step_deadline_s": w["step_deadline_s"]}
            assert cat.compile(doc).card_id == doc["card_id"]


def test_a_verdict_must_be_explicit():
    ok = {"schema": "minimoi.partnership.review_verdict/1", "verdict": "approve",
          "reviewed_set_digest": "sha256:" + "a" * 64, "findings_digest": None}
    assert contracts.errors("review_verdict", "verdict", ok) == []
    for bad in (dict(ok, verdict="lgtm"), {k: v for k, v in ok.items() if k != "verdict"}, dict(ok, extra=1)):
        assert contracts.errors("review_verdict", "verdict", bad)
