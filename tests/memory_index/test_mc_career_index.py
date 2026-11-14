"""MC's Vault index (#594): career material filtered out at index time, served only by a profile-checked Ask. Synthetic records only."""
import json
import os
import sqlite3
import subprocess

import pytest

from core.memory_ask import identity, service
from core.memory_index import career_filter, indexer, mc_index, policy, search, store
from core.memory_sync import apply
from core.memory_backup.backup import BackupError
from .helpers import add_record, make_shelf

RAW = policy.Caller(tier="all", scopes=("robert",), agent="mc", entry_point="tool")
CAREER = "Help me tighten my resume and the cover letter for the recruiter."


class NoModel:
    def release(self):
        pass

    def check_pin(self):
        from core.memory_index.embedder import EmbedderUnavailable
        raise EmbedderUnavailable("keyword only")


@pytest.fixture
def no_vec(monkeypatch):
    for mod in (store, indexer, search):
        monkeypatch.setattr(mod, "sqlite_vec", None)


def rules(tmp_path, name="rules.json", mode=0o600, **doc):
    path = tmp_path / name
    path.write_text(json.dumps({"schema": career_filter.SCHEMA, **doc}))
    os.chmod(path, mode)
    return path


def shelf_world(tmp_path):
    shelf = make_shelf(tmp_path)
    ids = {
        # Engineering talk with one career aside: the aside and its neighbours go, the rest stays findable.
        "mixed": add_record(shelf, "claude-code", "cc-mixed", [
            ("human", "Why does the vault-apply index runner hold the CoS records?"),
            ("assistant", "The pinned portal image predates the provider table."),
            ("human", "Unrelated: " + CAREER),
            ("assistant", "Sure, send both drafts and I will mark them up."),
            ("human", "Back to work: rebuild the keyword index after the runner moves."),
            ("assistant", "Run vault-apply with the rebuild flag once."),
            ("human", "And the turn after that one stays too."),
            ("assistant", "Yes, the window is one turn each side.")], normalizer=3),
        # A conversation that is mostly career: held whole.
        "career": add_record(shelf, "grok", "g-career", [
            ("human", "Draft a cover letter for the hiring manager."),
            ("assistant", "Here is a cover letter that leads with the platform work."),
            ("human", "Now the job search plan for next month."),
            ("assistant", "Week one: recruiters and the LinkedIn profile.")]),
        "plain": add_record(shelf, "codex", "c-plain", [
            ("human", "Add the quartz telemetry probe to the health job."),
            ("assistant", "Done: the quartz probe reports every five minutes.")], normalizer=5),
    }
    return shelf, ids


def keyword(shelf, index, q):
    return search.search(q, shelf_root=shelf.root, embedder=NoModel(), index_path=index, caller=RAW, k=5)["keyword"]


# ── the rules ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_generic_rules_load_and_match_career_words_not_engineering_ones():
    f = career_filter.load()
    for text in ("Talked to a recruiter", "updated my resume for them", "the CV is ready", "job applications", "cover letter"):
        assert f.matches(text), text
    for text in ("resume the build", "CVE fix", "career-sync mounts", "background check job", "HTTP interview handler"):
        assert not f.matches(text), text


def test_private_rules_merge_stricter_and_must_be_owner_only(tmp_path):
    base = rules(tmp_path, "base.json", terms=["recruiter"], record_threshold=3, record_fraction=0.2, turn_window=1)
    private = rules(tmp_path, "private.json", terms=["Acme Rockets"], record_threshold=2, record_fraction=0.1, turn_window=2,
                    exclude_providers=["minimoi-cos"])
    f = career_filter.load(base, private)
    assert f.matches("an offer from acme rockets") and f.matches("a recruiter")
    assert (f.record_threshold, f.record_fraction, f.turn_window, f.has_private) == (2, 0.1, 2, True)
    assert f.exclude_providers == {"minimoi-cos"}
    assert f.digest != career_filter.load(base).digest
    os.chmod(private, 0o644)
    with pytest.raises(career_filter.FilterError) as exc:
        career_filter.load(base, private)
    assert exc.value.code == "filter_insecure"


@pytest.mark.parametrize("doc", [{"terms": ["("]}, {"terms": "recruiter"}, {"record_threshold": 0}, {"extra": 1},
                                 {"exclude_records": ["not-an-id"]}, {"record_fraction": 2}])
def test_malformed_rules_are_refused(tmp_path, doc):
    with pytest.raises(career_filter.FilterError) as exc:
        career_filter.load(rules(tmp_path, **doc))
    assert exc.value.code == "filter_invalid"


def test_production_refuses_without_the_private_rules(tmp_path):
    with pytest.raises(career_filter.FilterError) as exc:
        career_filter.load(None, None, require_private=True)
    assert exc.value.code == "filter_missing"
    with pytest.raises(career_filter.FilterError):
        career_filter.load(None, tmp_path / "absent.json", require_private=True)


# ── index time ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_mc_index_never_holds_career_turns_but_keeps_the_rest(tmp_path, no_vec):
    shelf, ids = shelf_world(tmp_path)
    f = career_filter.load()
    full, mc = tmp_path / "full.sqlite", tmp_path / "mc.sqlite"
    assert indexer.update(shelf.root, None, index_path=full, keyword_only=True, min_free_bytes=0)["code"] == "ok"
    out = indexer.update(shelf.root, None, index_path=mc, keyword_only=True, min_free_bytes=0, career=f)
    assert out["code"] == "ok" and out["held"] == {"career_excluded": 1} and out["career_turns_excluded"] >= 3
    for q in ("resume", "cover letter", "recruiter", "drafts", "hiring manager", "LinkedIn"):
        assert keyword(shelf, full, q), q                                  # the full index (CoS) still finds it
        assert keyword(shelf, mc, q) == [], q                              # MC never can
    hits = keyword(shelf, mc, "vault-apply runner")
    assert hits and {h["record_id"] for h in hits} == {ids["mixed"]}
    assert keyword(shelf, mc, "turn after that one stays")                 # outside the window: kept
    assert keyword(shelf, mc, "quartz telemetry probe")[0]["chair"] == "Codex"
    con = store.connect(mc, readonly=True)
    try:
        meta = store.get_meta(con)
        assert meta["profile"] == "mc" and meta["career_filter_digest"] == f.digest
        assert con.execute("SELECT state, reason FROM records WHERE record_id=?", (ids["career"],)).fetchone() == \
            ("held", "career_excluded")
    finally:
        con.close()


def test_a_filtered_index_is_never_patched_unfiltered_or_with_other_rules(tmp_path, no_vec):
    shelf, _ = shelf_world(tmp_path)
    f = career_filter.load()
    mc, full = tmp_path / "mc.sqlite", tmp_path / "full.sqlite"
    indexer.update(shelf.root, None, index_path=mc, keyword_only=True, min_free_bytes=0, career=f)
    indexer.update(shelf.root, None, index_path=full, keyword_only=True, min_free_bytes=0)
    assert indexer.update(shelf.root, None, index_path=mc, keyword_only=True, min_free_bytes=0)["code"] == indexer.CAREER_MISMATCH
    other = career_filter.load(rules(tmp_path, terms=["zebra"]))
    assert indexer.update(shelf.root, None, index_path=mc, keyword_only=True, min_free_bytes=0, career=other)["code"] == \
        indexer.CAREER_MISMATCH
    assert indexer.update(shelf.root, None, index_path=full, keyword_only=True, min_free_bytes=0, career=f)["code"] == \
        indexer.CAREER_MISMATCH


def test_excluded_providers_and_owner_listed_records_are_held_whole(tmp_path, no_vec):
    shelf, ids = shelf_world(tmp_path)
    f = career_filter.load(rules(tmp_path, terms=["recruiter"], exclude_providers=["codex"], exclude_records=[ids["mixed"]]))
    mc = tmp_path / "mc.sqlite"
    out = indexer.update(shelf.root, None, index_path=mc, keyword_only=True, min_free_bytes=0, career=f)
    assert out["held"] == {"career_excluded": 2}
    assert keyword(shelf, mc, "quartz") == [] and keyword(shelf, mc, "vault-apply") == []


def test_a_record_held_for_an_unknown_provider_heals_when_the_code_learns_it(tmp_path, no_vec, monkeypatch):
    shelf, ids = shelf_world(tmp_path)
    full = tmp_path / "full.sqlite"
    table = dict(policy.MIN_NORMALIZER)
    monkeypatch.setattr("core.memory_shelf.sessions.RETRIEVAL_MIN_NORMALIZER", {k: v for k, v in table.items() if k != "codex"})
    monkeypatch.setattr(policy, "MIN_NORMALIZER", {k: v for k, v in table.items() if k != "codex"})
    assert indexer.update(shelf.root, None, index_path=full, keyword_only=True, min_free_bytes=0)["held"] == \
        {policy.UNKNOWN_PROVIDER: 1}
    monkeypatch.setattr("core.memory_shelf.sessions.RETRIEVAL_MIN_NORMALIZER", table)
    monkeypatch.setattr(policy, "MIN_NORMALIZER", table)
    out = indexer.update(shelf.root, None, index_path=full, keyword_only=True, min_free_bytes=0, only=[ids["plain"]])
    assert out["held"] == {} and keyword(shelf, full, "quartz")            # same file, no new edition: indexed now


# ── the builder ────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_build_publishes_then_is_a_noop_for_the_same_release_and_rules(tmp_path, no_vec):
    shelf, _ = shelf_world(tmp_path)
    f = career_filter.load()
    out = tmp_path / "vault-mc" / "memory-mc.sqlite"
    first = mc_index.build(shelf.root, out, f, release="r1")
    assert first["code"] == "ok" and first["swapped"] and mc_index.verify(out, f) is None
    assert mc_index.build(shelf.root, out, f, release="r1")["code"] == "noop"
    again = mc_index.build(shelf.root, out, f, release="r2")
    assert again["code"] == "ok" and again.get("unchanged", 0) >= 2        # incremental from the published copy
    other = career_filter.load(rules(tmp_path, terms=["zebra"]))
    assert mc_index.build(shelf.root, out, other, release="r2")["code"] == "ok"   # new rules: rebuilt, not patched
    assert mc_index.verify(out, other) is None and mc_index.verify(out, f) == "verify_profile"
    assert sorted(p.name for p in out.parent.iterdir()) == [".memory-mc.sqlite.build.lock", "memory-mc.sqlite"]


def test_cli_refuses_without_private_rules_in_production(tmp_path, capsys):
    code = mc_index.main(["build", "--shelf", str(tmp_path), "--out", str(tmp_path / "x.sqlite"),
                          "--private", str(tmp_path / "absent.json"), "--require-private"])
    assert code == mc_index.EXIT_REFUSED and json.loads(capsys.readouterr().out) == {"code": "filter_missing"}
    assert not (tmp_path / "x.sqlite").exists()


# ── the Ask service ────────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_mc_service_refuses_an_unfiltered_index_and_serves_the_filtered_one(tmp_path, no_vec):
    shelf, _ = shelf_world(tmp_path)
    f = career_filter.load()
    full, mc = tmp_path / "full.sqlite", tmp_path / "mc.sqlite"
    indexer.update(shelf.root, None, index_path=full, keyword_only=True, min_free_bytes=0)
    indexer.update(shelf.root, None, index_path=mc, keyword_only=True, min_free_bytes=0, career=f)
    reg = tmp_path / "ask" / "agents-mc.json"
    token = identity.add_agent(reg, "mc", tiers=("curated", "all"))
    headers = {"Authorization": f"Bearer {token}"}
    body = {"query": "vault-apply runner", "include_raw": True}
    wrong = service.create_app(registry_path=reg, shelf_root=shelf.root, index_path=full, embedder_factory=NoModel,
                               log_path=tmp_path / "log.jsonl", require_profile="mc").test_client()
    refused = wrong.post("/v1/ask", json=body, headers=headers)
    assert refused.status_code == 503 and refused.get_json() == {"error": "index_unfiltered"}
    right = service.create_app(registry_path=reg, shelf_root=shelf.root, index_path=mc, embedder_factory=NoModel,
                               log_path=tmp_path / "log.jsonl", require_profile="mc").test_client()
    answer = right.post("/v1/ask", json=body, headers=headers)
    assert answer.status_code == 200
    passages = [h["passage"] for h in answer.get_json()["keyword"]]
    assert passages and not any(f.matches(p) for p in passages)
    plain = service.create_app(registry_path=reg, shelf_root=shelf.root, index_path=full, embedder_factory=NoModel,
                               log_path=tmp_path / "log.jsonl").test_client()
    assert plain.post("/v1/ask", json=body, headers=headers).status_code == 200   # CoS's service: no profile, unchanged


# ── the CoS fix: vault-apply's runner follows the running portal ────────────────────────────────────────────────────────
def test_the_index_runner_can_follow_the_running_portal_image():
    image = "sha256:" + "a" * 64
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=image + "\n", stderr="")

    runner = apply.make_index_runner("docker-container:minimoi-portal", run=run)
    assert runner.image == image and calls[0][:2] == ["docker", "inspect"] and calls[0][-1] == "minimoi-portal"
    with pytest.raises(BackupError):
        apply.make_index_runner("docker-container:-rm", run=run)
    with pytest.raises(BackupError) as exc:
        apply.make_index_runner("docker-container:minimoi-portal",
                                run=lambda argv, **kw: subprocess.CompletedProcess(argv, 1, stdout="", stderr="no such"))
    assert exc.value.code == "index_runner_container_unavailable"
