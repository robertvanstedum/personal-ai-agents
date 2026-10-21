"""P-L1 learning consolidation against fixture contracts (eligibility, claims, extractor, admission,
ingress). Standing v0.3.1 §§8a.1, 8a.3 and acceptance row P-14. Clocks are fixed."""
import pytest

from core.mc_standing import consolidation as c
from core.mc_standing.runner import IngressUncertain
from core.mc_standing.spool import Spool

NOW = "2026-10-21T09:40:00Z"
VERSIONS = {"extractor": "x1", "prompt": "p1"}


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
    """Shared claim store with durable phases, used by both the closeout path and this job."""

    def __init__(self):
        self.by_key, self.by_id, self.log = {}, {}, []

    def claim(self, key, *, owner, source_set):
        rec = self.by_key.get(key)
        if rec is None or rec["status"] == "released":
            cid = f"claim-{len(self.by_id) + 1}"
            rec = {"claim_id": cid, "key": key, "owner": owner, "status": "claimed", "phase": "claimed",
                   "result_ref": None}
            self.by_key[key], self.by_id[cid] = rec, rec
            return dict(rec)
        if rec["status"] == "claimed" and rec["owner"] == owner:
            return dict(rec)
        return dict(rec, status="in_progress" if rec["status"] == "claimed" else rec["status"])

    def advance(self, cid, phase, reservation_id=None, result_ref=None):
        self.log.append(("advance", cid, phase))
        self.by_id[cid].update(phase=phase, result_ref=result_ref or self.by_id[cid]["result_ref"])

    def release(self, cid, reason):
        self.log.append(("release", cid, reason))
        self.by_id[cid].update(status="released", phase=reason)

    def hold(self, cid, reason):
        self.log.append(("hold", cid, reason))
        self.by_id[cid].update(status="held", phase="held")

    def complete(self, cid, result_ref):
        self.log.append(("complete", cid, result_ref))
        self.by_id[cid].update(status="done", phase="done", result_ref=result_ref)

    def other_path_holds(self, key, status):
        self.by_key[key] = {"claim_id": "closeout-1", "key": key, "owner": "closeout", "status": status,
                            "phase": status, "result_ref": None}


class Extractor:
    def __init__(self, proposals, fail=None):
        self.proposals, self.calls, self.fail = proposals, 0, fail

    def extract(self, texts, refs):
        self.calls += 1
        if self.fail == "not_dispatched":
            raise c.NotDispatched()
        if self.fail == "timeout":
            raise TimeoutError()
        return {"proposals": self.proposals, "extractor_version": "x1", "usage": {"usd": 0.02}}


class Admission:
    def __init__(self, decision="admitted"):
        self.decision, self.reserved, self.released, self.settled = decision, {}, [], []

    def reserve(self, key, input_tokens, output_tokens):
        rid = self.reserved.setdefault(key, f"res-{len(self.reserved) + 1}")
        return {"status": self.decision, "reservation_id": rid}

    def release(self, rid):
        self.released.append(rid)

    def settle(self, rid, usage):
        self.settled.append((rid, usage))
        return {"status": "settled"}


def ack(event_id, state):
    return {"schema": "mc-standing-ack/1", "event_id": event_id, "state": state, "record_id": None,
            "work_id": None, "code": ""}


class Ingress:
    def __init__(self, mode="accept"):
        self.mode, self.store, self.posts = mode, {}, []

    def post(self, event):
        self.posts.append(event)
        eid = event["event_id"]
        if self.mode == "uncertain":
            raise IngressUncertain()
        if self.mode == "none":
            return None
        if self.mode == "deferred":
            return ack(eid, "deferred")
        if self.mode == "wrong_event":
            return ack("00000000-0000-0000-0000-000000000000", "accepted")
        if self.mode == "legacy":               # a bare state without schema or event id
            return {"state": "accepted"}
        self.store[eid] = ack(eid, "accepted")
        return ack(eid, "accepted")

    def get(self, event_id):
        return self.store.get(event_id)


class Crash(BaseException):
    pass


def src(i, size=40, edition="e1", digest=None):
    return {"id": f"stmt-{i}", "edition": edition, "digest": digest or f"{i:064x}", "seq": i}, "x" * size


def setup(n=3, size=40, **kw):
    pairs = [src(i, size) for i in range(1, n + 1)]
    return Elig([p[0] for p in pairs], {p[0]["id"]: p[1] for p in pairs}, **kw)


def lesson():
    return Extractor([{"kind": "lesson_proposal", "payload": {"t": 1}}])


def run(tmp_path, elig, claims=None, extractor=None, admission=None, ingress=None, **kw):
    spool = kw.pop("spool", None) or Spool(tmp_path / "state")
    out = c.run_consolidation(spool=spool, eligibility=elig, claims=claims if claims is not None else Claims(),
                              extractor=extractor or lesson(), admission=admission or Admission(),
                              ingress=ingress or Ingress(), principal="robert", environment="prod",
                              policy_hash="a" * 64, versions=VERSIONS, now_iso=NOW,
                              enabled=kw.pop("enabled", True), **kw)
    return spool, out


def state(spool):
    return spool.read("cycles", c.STATE_NAME) or {}


def test_disabled_by_default(tmp_path):
    spool = Spool(tmp_path / "state")
    out = c.run_consolidation(spool=spool, eligibility=setup(), claims=Claims(), extractor=lesson(),
                              admission=Admission(), ingress=Ingress(), principal="robert",
                              environment="prod", policy_hash="a" * 64, versions={}, now_iso=NOW)
    assert out == {"state": "disabled"}


def test_happy_path_walks_every_phase_and_advances_watermark(tmp_path):
    elig, ing, claims, adm = setup(), Ingress(), Claims(), Admission()
    spool, out = run(tmp_path, elig, claims=claims, admission=adm, ingress=ing)
    assert out["state"] == "delivered" and out["events"] == 1
    ev = ing.posts[0]
    assert ev["schema"] == "mc-learning-event/1" and ev["kind"] == "lesson_proposal"
    assert [r["id"] for r in ev["source_refs"]] == ["stmt-1", "stmt-2", "stmt-3"]
    assert [x[2] for x in claims.log] == ["reserved", "calling", "extracted", claims.log[-1][2]]
    assert claims.log[-1][0] == "complete" and adm.settled == [("res-1", {"usd": 0.02})]
    assert out["settlement"] == "settled" and state(spool)["unsettled"] == []
    assert state(spool)["cursor"] == 3 and state(spool)["current"] is None
    _, out = run(tmp_path, elig, spool=spool, claims=claims, ingress=ing)
    assert out["state"] == "nothing_new" and elig.cursors[-1] == 3


def test_same_sources_twice_give_same_event_ids_and_no_second_extraction(tmp_path):
    claims, ext = Claims(), lesson()
    _, first = run(tmp_path / "a", setup(), claims=claims, extractor=ext, ingress=(ing := Ingress()))
    _, again = run(tmp_path / "b", setup(), claims=claims, extractor=ext, ingress=ing)   # fresh spool, same store
    assert first["state"] == "delivered" and again == {"state": "skipped", "reason": "extraction_done"}
    assert ext.calls == 1 and len(ing.posts) == 1


def test_unknown_or_disabled_kinds_and_oversize_payloads_are_never_forwarded(tmp_path):
    ext = Extractor([{"kind": "adoption", "payload": {}}, {"kind": "reflection_note", "payload": {}},
                     {"kind": "lesson_proposal", "payload": {"t": "x" * (c.MAX_PAYLOAD_BYTES + 1)}},
                     {"kind": "lesson_proposal", "payload": "not an object"},
                     {"kind": "challenge", "payload": {"reason_code": "forbidden_candidate"}}])
    ing = Ingress()
    run(tmp_path, setup(), extractor=ext, ingress=ing)
    assert [e["kind"] for e in ing.posts] == ["challenge"]


def test_ten_record_overflow_stays_queued_for_the_next_night(tmp_path):
    elig, claims = setup(n=12), Claims()
    spool, out = run(tmp_path, elig, claims=claims)
    assert out["state"] == "delivered" and state(spool)["cursor"] == 10
    ing = Ingress()
    _, out = run(tmp_path, elig, spool=spool, claims=claims, ingress=ing)
    assert [r["id"] for r in ing.posts[0]["source_refs"]] == ["stmt-11", "stmt-12"]
    assert state(spool)["cursor"] == 12


def test_token_bound_takes_a_prefix_and_leaves_the_rest_queued(tmp_path):
    elig = setup(n=5, size=16_000)          # ~4,000 tokens each; 10,500 available after allowance
    spool, out = run(tmp_path, elig)
    assert state(spool)["cursor"] == 2 and out["state"] == "delivered"


@pytest.mark.parametrize("decision", ["deferred", "refused"])
def test_budget_not_admitted_releases_claim_and_reservation_and_advances_nothing(tmp_path, decision):
    ext, claims, adm = lesson(), Claims(), Admission(decision)
    spool, out = run(tmp_path, setup(), extractor=ext, claims=claims, admission=adm)
    assert out["reason"] == "budget_not_admitted" and ext.calls == 0
    assert state(spool).get("cursor") is None and state(spool)["current"] is None
    assert ("release", "claim-1", "budget_not_admitted") in claims.log and adm.released == ["res-1"]
    adm.decision = "admitted"                              # next night: same sources, not stranded
    _, out = run(tmp_path, setup(), spool=spool, extractor=ext, claims=claims, admission=adm)
    assert out["state"] == "delivered" and ext.calls == 1


def test_pause_after_claim_releases_it(tmp_path):
    checks = iter([False] * 4 + [True] * 10)                # pass the read checks, then pause
    claims, ext = Claims(), lesson()
    spool, out = run(tmp_path, setup(), claims=claims, extractor=ext, paused=lambda: next(checks))
    assert out["state"] == "paused" and ext.calls == 0
    assert claims.by_id["claim-1"]["status"] == "released" and state(spool)["current"] is None


def test_restart_before_the_call_releases_and_starts_over(tmp_path):
    claims, adm, ext = Claims(), Admission(), lesson()

    class CrashingAdmission(Admission):
        def reserve(self, *a):
            raise Crash()
    with pytest.raises(Crash):
        run(tmp_path, setup(), claims=claims, admission=CrashingAdmission(), extractor=ext)
    spool = Spool(tmp_path / "state")
    assert state(spool)["current"]["phase"] == "claimed"
    _, out = run(tmp_path, setup(), spool=spool, claims=claims, admission=adm, extractor=ext)
    assert out["state"] == "delivered" and ext.calls == 1
    assert ("release", "claim-1", "restart_before_call") in claims.log


def test_crash_during_the_model_call_is_held_never_called_again(tmp_path):
    claims, ext = Claims(), lesson()

    class CrashingExtractor(Extractor):
        def extract(self, *a):
            self.calls += 1
            raise Crash()
    crashing = CrashingExtractor([])
    with pytest.raises(Crash):
        run(tmp_path, setup(), claims=claims, extractor=crashing)
    spool = Spool(tmp_path / "state")
    _, out = run(tmp_path, setup(), spool=spool, claims=claims, extractor=ext)
    assert out == {"state": "held", "reason": "model_call_outcome_unknown", "key": out["key"]}
    _, out = run(tmp_path, setup(), spool=spool, claims=claims, extractor=ext)
    assert out["state"] == "held" and ext.calls == 0 and crashing.calls == 1
    assert claims.by_id["claim-1"]["status"] == "held"


def test_model_error_with_unknown_spend_is_held_and_keeps_the_reservation(tmp_path):
    adm, claims = Admission(), Claims()
    spool, out = run(tmp_path, setup(), extractor=Extractor([], fail="timeout"), admission=adm, claims=claims)
    assert out["state"] == "held" and adm.released == [] and adm.settled == []
    res = c.clear_held(spool=spool, claims=claims, operator="robert", disposition="release")
    assert res["state"] == "released" and state(spool)["current"] is None
    assert spool.read("findings", f"learning-held-disposition.{out['key'][:40]}")["operator"] == "robert"


def test_not_dispatched_is_a_clean_deferral(tmp_path):
    adm, claims = Admission(), Claims()
    spool, out = run(tmp_path, setup(), extractor=Extractor([], fail="not_dispatched"), admission=adm, claims=claims)
    assert out == {"state": "deferred", "reason": "extractor_not_dispatched"}
    assert adm.released == ["res-1"] and claims.by_id["claim-1"]["status"] == "released"


def test_result_written_before_events_so_restart_resends_same_ids(tmp_path):
    claims, ext = Claims(), lesson()

    class CrashOnAdvance(Claims):
        def advance(self, cid, phase, **kw):
            super().advance(cid, phase, **kw)
            if phase == "extracted":
                raise Crash()
    crashing = CrashOnAdvance()
    with pytest.raises(Crash):
        run(tmp_path, setup(), claims=crashing, extractor=ext)
    spool = Spool(tmp_path / "state")
    ing = Ingress()
    _, out = run(tmp_path, setup(), spool=spool, claims=crashing, extractor=ext, ingress=ing)
    assert out["state"] == "delivered" and ext.calls == 1


def test_crash_after_result_but_before_extracted_phase_resumes_from_the_result(tmp_path):
    claims, ext = Claims(), lesson()
    real_put = Spool.put_immutable

    def crash_after_result(self, kind, name, doc):
        written = real_put(self, kind, name, doc)
        if name.startswith("learning-result."):
            raise Crash()
        return written
    Spool.put_immutable = crash_after_result
    try:
        with pytest.raises(Crash):
            run(tmp_path, setup(), claims=claims, extractor=ext)
    finally:
        Spool.put_immutable = real_put
    spool = Spool(tmp_path / "state")
    assert state(spool)["current"]["phase"] == "calling"
    ing = Ingress()
    _, out = run(tmp_path, setup(), spool=spool, claims=claims, extractor=ext, ingress=ing)
    assert out["state"] == "delivered" and ext.calls == 1 and len(ing.posts) == 1


def test_uncertain_delivery_resumes_with_same_event_ids_and_no_second_call(tmp_path):
    elig, ext, ing = setup(), lesson(), Ingress("uncertain")
    claims = Claims()
    spool, out = run(tmp_path, elig, extractor=ext, ingress=ing, claims=claims)
    assert out["state"] == "delivery_unknown" and state(spool).get("cursor") is None
    ing.mode = "accept"
    _, out = run(tmp_path, elig, spool=spool, extractor=ext, ingress=ing, claims=claims)
    assert out["state"] == "delivered" and ext.calls == 1
    assert len({e["event_id"] for e in ing.posts}) == 1


@pytest.mark.parametrize("mode", ["none", "wrong_event", "legacy"])
def test_a_missing_or_foreign_ack_is_not_acknowledgement(tmp_path, mode):
    claims = Claims()
    spool, out = run(tmp_path, setup(), ingress=Ingress(mode), claims=claims)
    assert out["state"] == "delivery_unknown" and out["code"] == "ack_invalid"
    assert state(spool).get("cursor") is None and state(spool)["current"]["phase"] == "extracted"
    assert not any(x[0] == "complete" for x in claims.log)


def test_deferred_stays_pending_and_completes_only_on_a_committed_ack(tmp_path):
    """Codex L-F1: a deferred ack advanced the cursor and completed the claim."""
    ing, claims, ext = Ingress("deferred"), Claims(), lesson()
    spool, out = run(tmp_path, setup(), ingress=ing, claims=claims, extractor=ext)
    assert out["state"] == "delivery_pending" and state(spool).get("cursor") is None
    assert not any(x[0] == "complete" for x in claims.log)
    ing.mode = "accept"
    _, out = run(tmp_path, setup(), spool=spool, ingress=ing, claims=claims, extractor=ext)
    assert out["state"] == "delivered" and state(spool)["cursor"] == 3 and ext.calls == 1


def test_settlement_outage_keeps_a_durable_queue_and_retries(tmp_path):
    """Codex L-F2: a swallowed settlement error used to lose the reservation pointer."""
    class FlakyAdmission(Admission):
        def __init__(self):
            super().__init__()
            self.down = True

        def settle(self, rid, usage):
            if self.down:
                raise ConnectionError("gateway down")
            return super().settle(rid, usage)
    adm, claims = FlakyAdmission(), Claims()
    spool, out = run(tmp_path, setup(), admission=adm, claims=claims)
    assert out["state"] == "delivered" and out["settlement"] == "pending"
    assert state(spool)["unsettled"] == [{"reservation_id": "res-1", "result_ref": out_ref(spool)}]
    adm.down = False
    _, out = run(tmp_path, setup(), spool=spool, admission=adm, claims=claims)
    assert out["state"] == "nothing_new" and state(spool)["unsettled"] == []
    assert adm.settled == [("res-1", {"usd": 0.02})]


def out_ref(spool):
    return [n for n in spool.names("delivery") if n.startswith("learning-result.")][0]


def test_crash_during_event_preparation_replays_identical_events(tmp_path):
    """Codex L-F3: occurred_at/policy/environment must be frozen before the first outbox write."""
    ext = Extractor([{"kind": "lesson_proposal", "payload": {"a": 1}}, {"kind": "challenge", "payload": {"b": 2}}])
    claims = Claims()
    real_put = Spool.put_immutable
    count = {"n": 0}

    class Crash(BaseException):
        pass

    def crash_on_second_event(self, kind, name, doc):
        written = real_put(self, kind, name, doc)
        if kind == "outbox":
            count["n"] += 1
            if count["n"] == 1:
                raise Crash()
        return written
    Spool.put_immutable = crash_on_second_event
    try:
        with pytest.raises(Crash):
            run(tmp_path, setup(), extractor=ext, claims=claims)
    finally:
        Spool.put_immutable = real_put
    spool = Spool(tmp_path / "state")
    ing = Ingress()
    out = c.run_consolidation(spool=spool, eligibility=setup(), claims=claims, extractor=ext,
                              admission=Admission(), ingress=ing, principal="robert", environment="prod",
                              policy_hash="b" * 64, versions=VERSIONS, now_iso="2026-10-22T09:40:00Z",
                              enabled=True)
    assert out["state"] == "delivered" and ext.calls == 1
    assert {e["occurred_at"] for e in ing.posts} == {NOW} and {e["policy_hash"] for e in ing.posts} == {"a" * 64}


def test_closeout_contention(tmp_path):
    elig = setup()
    key = c.extraction_key("robert", "prod", elig.sources, VERSIONS)
    ext = lesson()
    for status, advances in (("done", True), ("claimed", False), ("held", False)):
        claims = Claims()
        claims.other_path_holds(key, status)
        spool, out = run(tmp_path / status, elig, claims=claims, extractor=ext)
        assert out["state"] == "skipped" and ext.calls == 0
        assert (state(spool).get("cursor") == 3) is advances


def test_same_content_from_a_different_source_or_edition_is_a_new_key(tmp_path):
    a, _ = src(1)
    b, _ = src(2, digest=a["digest"])
    a2, _ = src(1, edition="e2")
    keys = {c.extraction_key("robert", "prod", [s], VERSIONS) for s in (a, b, a2)}
    assert len(keys) == 3
    assert c.extraction_key("robert", "prod", [a], VERSIONS) != c.extraction_key("robert", "prod", [a], dict(VERSIONS, prompt="p2"))


def test_source_with_an_older_timestamp_but_later_commit_is_not_skipped(tmp_path):
    elig = setup(n=2)
    spool, _ = run(tmp_path, elig, claims=(claims := Claims()))
    late, text = src(3)
    late["occurred_at"] = "2026-01-01T00:00:00Z"        # older occurrence, newer commit sequence
    elig.sources.append(late)
    elig.texts[late["id"]] = text
    ing = Ingress()
    run(tmp_path, elig, spool=spool, claims=claims, ingress=ing)
    assert [r["id"] for r in ing.posts[0]["source_refs"]] == ["stmt-3"]


def test_out_of_order_snapshot_is_refused_without_advancing(tmp_path):
    elig = setup(n=3)
    elig.sources[1], elig.sources[2] = elig.sources[2], elig.sources[1]
    spool, out = run(tmp_path, elig)
    assert out == {"state": "invalid_snapshot", "reason": "sources_not_in_commit_order"}
    assert state(spool).get("cursor") is None


def test_revoked_and_missing_sources_get_durable_dispositions_without_content(tmp_path):
    elig = setup(revoked={"stmt-2"})
    elig.texts["stmt-3"] = None
    ing = Ingress()
    spool, out = run(tmp_path, elig, ingress=ing)
    assert out["revoked"] == 2
    assert [r["id"] for r in ing.posts[0]["source_refs"]] == ["stmt-1"]
    d = spool.read("findings", "learning-disposition.stmt-2.e1")
    assert d == {"id": "stmt-2", "edition": "e1", "reason_code": "revoked"}
    assert spool.read("findings", "learning-disposition.stmt-3.e1")["reason_code"] == "missing"


def test_all_sources_revoked_makes_no_call_and_advances(tmp_path):
    ext = lesson()
    spool, out = run(tmp_path, setup(revoked={"stmt-1", "stmt-2", "stmt-3"}), extractor=ext)
    assert out == {"state": "nothing_readable", "revoked": 3} and ext.calls == 0
    assert state(spool)["cursor"] == 3


def test_kill_is_honoured_at_every_checkpoint(tmp_path):
    for n in range(0, 8):
        calls = {"n": 0}

        def kill():
            calls["n"] += 1
            return calls["n"] > n
        ext, ing, claims = lesson(), Ingress(), Claims()
        spool, out = run(tmp_path / f"k{n}", setup(), extractor=ext, ingress=ing, claims=claims, kill=kill)
        if out["state"] == "paused":
            assert not any(cl[0] == "complete" for cl in claims.log)
            held = [r for r in claims.by_id.values() if r["status"] == "claimed" and r["phase"] in ("claimed", "reserved")]
            assert not held                                  # never a stranded pre-call claim
        if ext.calls == 0:
            assert ing.posts == []


def test_pause_and_kill_stop_before_reads(tmp_path):
    ext = lesson()
    _, out = run(tmp_path, setup(), extractor=ext, paused=lambda: True)
    assert out["state"] == "paused" and ext.calls == 0
    _, out = run(tmp_path / "k", setup(), extractor=ext, kill=lambda: True)
    assert out["state"] == "paused" and ext.calls == 0



def test_missing_result_evidence_is_held_not_settled_as_zero(tmp_path):
    """Codex delta review of 113d1cf6: a missing result used to be settled with usage None and
    dropped from the queue."""
    adm, claims = Admission(), Claims()
    spool, out = run(tmp_path, setup(), admission=adm, claims=claims)
    st = state(spool)
    st["unsettled"] = [{"reservation_id": "res-x", "result_ref": "learning-result.missing"}]
    spool.put_state("cycles", c.STATE_NAME, st)
    adm.settled.clear()
    run(tmp_path, setup(), spool=spool, admission=adm, claims=claims)
    assert adm.settled == []
    assert state(spool)["unsettled"][0]["held"] == "result_or_usage_missing"


@pytest.mark.parametrize("reply", [None, {}, {"status": "accepted"}, "settled"])
def test_only_an_explicit_settled_reply_clears_the_queue(tmp_path, reply):
    class Vague(Admission):
        def settle(self, rid, usage):
            self.settled.append((rid, usage))
            return reply
    adm = Vague()
    spool, out = run(tmp_path, setup(), admission=adm)
    assert out["settlement"] == "pending" and len(state(spool)["unsettled"]) == 1
