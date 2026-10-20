"""P-L1 learning consolidation against fixture contracts (eligibility, claims, extractor, admission,
ingress). Standing v0.3.1 §§8a.1, 8a.3."""
import pytest

from core.mc_standing import consolidation as c
from core.mc_standing.runner import IngressUncertain
from core.mc_standing.spool import Spool

NOW = "2026-10-21T09:40:00Z"


class Elig:
    def __init__(self, sources, texts, revoked=()):
        self.sources, self.texts, self.revoked = sources, texts, set(revoked)
        self.cursors = []

    def eligible(self, cursor, limit):
        self.cursors.append(cursor)
        rest = [s for s in self.sources if cursor is None or s["seq"] > cursor][:limit]
        return {"snapshot_id": "snap-1", "sources": rest, "learning_refs": [], "policy_version": "p1",
                "next_cursor": rest[-1]["seq"] if rest else cursor, "coverage": "complete"}

    def read(self, snap, sid):
        if sid in self.revoked:
            raise c.Revoked()
        return self.texts[sid]


class Claims:
    def __init__(self, preset=None):
        self.state, self.preset = {}, preset or {}

    def claim(self, key):
        if key in self.preset:
            return self.preset[key]
        if self.state.get(key) == "done":
            return "done"
        if key in self.state:
            return "in_progress"
        self.state[key] = "claimed"
        return "claimed"

    def complete(self, key, result_ref):
        self.state[key] = "done"


class Extractor:
    def __init__(self, proposals):
        self.proposals, self.calls = proposals, 0

    def extract(self, texts, refs):
        self.calls += 1
        return self.proposals, "x1"


class Admission:
    def __init__(self, decision="admitted"):
        self.decision, self.calls = decision, []

    def reserve(self, input_tokens, output_tokens):
        self.calls.append((input_tokens, output_tokens))
        return self.decision


class Ingress:
    def __init__(self, mode="accept"):
        self.mode, self.store, self.posts = mode, {}, []

    def post(self, event):
        self.posts.append(event)
        if self.mode == "uncertain":
            raise IngressUncertain()
        self.store[event["event_id"]] = {"state": "accepted"}
        return {"state": "accepted"}

    def get(self, event_id):
        return self.store.get(event_id)


def src(i, size=40):
    return {"id": f"stmt-{i}", "edition": "e1", "digest": f"{i:064x}", "seq": i}, "x" * size


def setup(n=3, size=40, **kw):
    pairs = [src(i, size) for i in range(1, n + 1)]
    return Elig([p[0] for p in pairs], {p[0]["id"]: p[1] for p in pairs}, **kw)


def run(tmp_path, elig, claims=None, extractor=None, admission=None, ingress=None, **kw):
    spool = kw.pop("spool", None) or Spool(tmp_path / "state")
    out = c.run_consolidation(spool=spool, eligibility=elig, claims=claims or Claims(),
                              extractor=extractor or Extractor([{"kind": "lesson_proposal", "payload": {"t": 1}}]),
                              admission=admission or Admission(), ingress=ingress or Ingress(),
                              principal="robert", environment="prod", policy_hash="a" * 64,
                              versions={"extractor": "x1", "prompt": "p1"}, now_iso=NOW,
                              enabled=kw.pop("enabled", True), **kw)
    return spool, out


def test_disabled_by_default(tmp_path):
    spool = Spool(tmp_path / "state")
    out = c.run_consolidation(spool=spool, eligibility=setup(), claims=Claims(), extractor=Extractor([]),
                              admission=Admission(), ingress=Ingress(), principal="robert",
                              environment="prod", policy_hash="a" * 64, versions={}, now_iso=NOW)
    assert out == {"state": "disabled"}


def test_happy_path_emits_proposals_and_advances_watermark(tmp_path):
    elig, ing = setup(), Ingress()
    spool, out = run(tmp_path, elig, ingress=ing)
    assert out["state"] == "delivered" and out["events"] == 1
    ev = ing.posts[0]
    assert ev["schema"] == "mc-learning-event/1" and ev["kind"] == "lesson_proposal"
    assert [r["id"] for r in ev["source_refs"]] == ["stmt-1", "stmt-2", "stmt-3"]
    assert spool.read("cycles", c.STATE_NAME)["cursor"] == 3
    _, out = run(tmp_path, elig, spool=spool, ingress=ing)
    assert out["state"] == "nothing_new" and elig.cursors[-1] == 3


def test_unknown_or_disabled_kinds_are_never_forwarded(tmp_path):
    ext = Extractor([{"kind": "adoption", "payload": {}}, {"kind": "reflection_note", "payload": {}},
                     {"kind": "challenge", "payload": {"reason_code": "forbidden_candidate"}}])
    ing = Ingress()
    run(tmp_path, setup(), extractor=ext, ingress=ing)
    assert [e["kind"] for e in ing.posts] == ["challenge"]


def test_token_bound_takes_a_prefix_and_leaves_the_rest_queued(tmp_path):
    elig = setup(n=5, size=16_000)          # ~4,000 tokens each; 10,500 available after allowance
    spool, out = run(tmp_path, elig)
    assert spool.read("cycles", c.STATE_NAME)["cursor"] == 2
    assert out["state"] == "delivered"


def test_budget_refusal_advances_nothing_and_makes_no_call(tmp_path):
    ext = Extractor([{"kind": "lesson_proposal", "payload": {}}])
    spool, out = run(tmp_path, setup(), extractor=ext, admission=Admission("deferred"))
    assert out["reason"] == "budget_not_admitted" and ext.calls == 0
    assert (spool.read("cycles", c.STATE_NAME) or {}).get("cursor") is None


def test_closeout_already_extracted_same_sources_is_skipped(tmp_path):
    elig = setup()
    key = c.extraction_key("robert", "prod", elig.sources, {"extractor": "x1", "prompt": "p1"})
    ext = Extractor([{"kind": "lesson_proposal", "payload": {}}])
    spool, out = run(tmp_path, elig, claims=Claims({key: "done"}), extractor=ext)
    assert out["reason"] == "extraction_done" and ext.calls == 0
    assert spool.read("cycles", c.STATE_NAME)["cursor"] == 3
    _, out = run(tmp_path / "b", elig, claims=Claims({key: "in_progress"}), extractor=ext)
    assert out["reason"] == "extraction_in_progress" and ext.calls == 0


def test_uncertain_delivery_resumes_without_a_second_paid_call(tmp_path):
    elig, ext, ing = setup(), Extractor([{"kind": "lesson_proposal", "payload": {}}]), Ingress("uncertain")
    spool, out = run(tmp_path, elig, extractor=ext, ingress=ing)
    assert out["state"] == "delivery_unknown"
    assert spool.read("cycles", c.STATE_NAME)["cursor"] is None          # not advanced yet
    ing.mode = "accept"
    _, out = run(tmp_path, elig, spool=spool, extractor=ext, ingress=ing)
    assert out["state"] == "delivered" and ext.calls == 1                 # no second model call
    assert len({e["event_id"] for e in ing.posts}) == 1


def test_revoked_source_is_recorded_without_content(tmp_path):
    elig = setup(revoked={"stmt-2"})
    ing = Ingress()
    spool, out = run(tmp_path, elig, ingress=ing)
    assert out["revoked"] == 1
    assert [r["id"] for r in ing.posts[0]["source_refs"]] == ["stmt-1", "stmt-3"]


def test_pause_and_kill_stop_before_reads(tmp_path):
    ext = Extractor([{"kind": "lesson_proposal", "payload": {}}])
    _, out = run(tmp_path, setup(), extractor=ext, paused=lambda: True)
    assert out["state"] == "paused" and ext.calls == 0
    _, out = run(tmp_path, setup(), extractor=ext, kill=lambda: True)
    assert out["state"] == "paused" and ext.calls == 0
