"""B3B: migration rehearsal CoS In flight <-> task service (spec v1.1 M01-M04; acceptance F09), revised after
REVIEW_B3B_INDEPENDENT_2026-11-04. REHEARSAL on copies of synthetic rows; nothing live is read or written and no
cutover is performed or implied."""
from __future__ import annotations

import json
import shutil

import pytest

from domains.cos.store import contracts as cos_contracts
from services.partnership_tasks.auth import Bindings, token_sha256
from services.partnership_tasks.joins import restore_check
from services.partnership_tasks.migration import (UNRESOLVED_LEAD, migrate, read_rows, reverse,
                                                  snapshot_legacy)
from services.partnership_tasks.service import DIRS
from tests.partnership.synth import Flow, cmd, ref, service

GRANT = {"store": "grants", "id": "grant-syn-migration-cutover", "edition": "1", "digest": None}
AT = "2026-11-05T09:00:00+00:00"


def row(n, status, with_, opened_by="cos", **over):
    r = {"schema": "minimoi.cos.in_flight/1", "data_scope": "personal", "environment": "dev",
         "item_id": f"IF-syn-{n}", "revision": 3, "kind": "action", "title": f"Synthetic item {n}",
         "title_provenance": "agent_draft", "priority": 2, "status": status, "with": with_,
         "opened_at": "2026-10-20T10:00:00+00:00", "last_moved_at": "2026-10-25T10:00:00+00:00",
         "closed_at": "2026-10-26T10:00:00+00:00" if status == "done" else None,
         "outcome": "done" if status == "done" else None, "source": None, "comments": [], "opened_by": opened_by}
    r.update(over)
    return r


ROWS = [row(1, "open", "robert", title_provenance="robert_source"),
        row(2, "doing", "cos", comments=[{"at": "2026-10-22T10:00:00+00:00", "author": "robert",
                                          "text": "Keep it short.", "provenance": "robert_source"}]),
        row(3, "waiting", "mc", opened_by="robert"),
        row(4, "done", "cos", close_note="Shipped."),
        row(5, "done", "mc", outcome="dropped")]


@pytest.fixture
def legacy(tmp_path):
    src = tmp_path / "legacy_copy"
    src.mkdir()
    for r in ROWS:                                     # the CoS store's own document format (indent=1)
        (src / f"{r['item_id']}.json").write_text(json.dumps(r, sort_keys=True, ensure_ascii=False, indent=1) + "\n")
    snapshot_legacy(src, tmp_path / "snapshot")
    return src, tmp_path / "snapshot"


def run(tmp_path, snapshot):
    svc = service(tmp_path / "tasks")
    out = migrate(svc, migration_token="tok-migration", owner_token="tok-principal", rows=read_rows(snapshot),
                  grant_ref=GRANT)
    return svc, out, {m["item_id"]: m for m in out["mapping"]}


def test_s2_1_the_unresolved_lead_marker_is_reserved(tmp_path, legacy):
    with pytest.raises(ValueError, match="reserved"):
        Bindings([{"binding_id": "bind-x", "token_sha256": token_sha256("t"), "seat": UNRESOLVED_LEAD,
                   "principal": "principal", "environment": "synthetic", "origin": "agent", "scopes": ["personal"],
                   "read_scopes": ["personal"], "operations": ["task.accept"]}])
    svc, out, m = run(tmp_path, legacy[1])
    t = svc.get("task", m["IF-syn-3"]["task_id"], "tok-principal")
    assert t["lead"] == UNRESOLVED_LEAD and t["waiting"] == {"reason": "input", "who_can_unblock": "principal"}


def test_s2_4_history_and_provenance_are_kept_and_the_principal_is_not_credited_with_agent_words(tmp_path, legacy):
    svc, out, m = run(tmp_path, legacy[1])
    assert out["problems"] == []
    se = svc.get("source_event", m["IF-syn-2"]["source_event_id"], "tok-principal")
    assert se["producer"] == "migration" and se["provenance"]["origin"] == "scheduler"
    interp = svc.get("interpretation", m["IF-syn-2"]["interpretation_id"], "tok-principal")
    assert interp["authority"] == {"kind": "existing_grant", "ref": GRANT}
    assert m["IF-syn-2"]["extra_source_events"][0]["provenance"] == "robert_source"
    assert m["IF-syn-4"]["extra_source_events"] == [{"source_event_id": "SE-mig-syn-4-close", "kind": "close_note"}]
    assert svc.get("request", m["IF-syn-5"]["request_id"], "tok-principal")["state"] == "withdrawn"   # dropped
    assert svc.get("request", m["IF-syn-4"]["request_id"], "tok-principal")["state"] == "answered"
    saved = json.loads(open(out["mapping_file"]).read())
    assert saved["IF-syn-1"]["row"]["title_provenance"] == "robert_source"                         # persisted


def test_s2_3_a_changed_row_is_refused_never_minted_twice(tmp_path, legacy):
    svc, out, m = run(tmp_path, legacy[1])
    again = migrate(svc, migration_token="tok-migration", owner_token="tok-principal", rows=read_rows(legacy[1]),
                    grant_ref=GRANT)
    assert again["problems"] == [] and len(list((tmp_path / "tasks" / "tasks").glob("*.json"))) == 3
    advanced = [dict(r, revision=4, title=r["title"] + " (edited)") if r["item_id"] == "IF-syn-1" else r
                for r in read_rows(legacy[1])]
    third = migrate(svc, migration_token="tok-migration", owner_token="tok-principal", rows=advanced, grant_ref=GRANT)
    assert third["problems"][0]["code"] == "row_changed_since_migration"
    assert len(list((tmp_path / "tasks" / "tasks").glob("*.json"))) == 3


def test_s3_bad_rows_are_problems_and_the_mapping_survives(tmp_path, legacy):
    svc = service(tmp_path / "tasks")
    rows = read_rows(legacy[1]) + [row(9, "open", "codex")]
    out = migrate(svc, migration_token="tok-migration", owner_token="tok-principal", rows=rows, grant_ref=GRANT)
    assert out["problems"] == [{"item_id": "IF-syn-9", "code": "unknown_legacy_seat", "detail": "codex"}]
    assert len(out["mapping"]) == 5


def test_s2_2_a_rollback_with_no_new_writes_is_byte_identical(tmp_path, legacy):
    src, snap = legacy
    svc, out, m = run(tmp_path, snap)
    reverse(svc, "tok-principal", read_rows(snap), out["mapping"], tmp_path / "rolled", source_folder=src, at=AT)
    for r in ROWS:
        assert (tmp_path / "rolled" / f"{r['item_id']}.json").read_bytes() == \
            (src / f"{r['item_id']}.json").read_bytes()


def test_f09_s2_5_rollback_after_new_writes_replays_them_and_lists_what_it_cannot_hold(tmp_path, legacy):
    src, snap = legacy
    svc, out, m = run(tmp_path, snap)
    f = Flow(svc)
    t2 = m["IF-syn-2"]["task_id"]
    t = svc.get("task", t2, "tok-principal")
    f.ok(cmd("task.transition", "task", t2, {"to": "canceled", "reason": "Dropped after cutover.", "waiting": None,
                                             "evidence_refs": [ref("records", "e")],
                                             "fence_generation": t["fence_generation"]}, expected=t["revision"]),
         "tok-principal")
    t3 = m["IF-syn-3"]["task_id"]
    t = svc.get("task", t3, "tok-principal")
    f.ok(cmd("task.assign", "task", t3, {"role": "lead", "seat": "mc", "reason": "Principal resolves the lead."},
             expected=t["revision"]), "tok-principal")
    for n, holder in (("0099", "mc"), ("0098", "codex")):
        f.source(f"SE-syn-{n}", f"New ask {n}.")
        f.interpret(f"IN-syn-{n}", [f"SE-syn-{n}"], f"New {n}.")
        f.adopt(f"IN-syn-{n}")
        f.request(f"RQ-syn-{n}", f"SE-syn-{n}", f"New {n}.", iid=f"IN-syn-{n}")
        f.promote(f"RQ-syn-{n}", f"TK-syn-{n}", lead="mc", holder=holder)
    t = svc.get("task", "TK-syn-0099", "tok-principal")
    f.ok(cmd("task.delegate", "task", "TK-syn-0099", {"child_task_id": "TK-syn-0099c1", "holder": "worker-a",
                                                      "outcome": "Step.", "done_when": "d", "return_window": None,
                                                      "fence_generation": t["fence_generation"]},
             expected=t["revision"]), "tok-mc")
    f.source("SE-syn-0097", "A quick question after cutover.")
    f.request("RQ-syn-0097", "SE-syn-0097", "Quick question.")
    back = reverse(svc, "tok-principal", read_rows(snap), out["mapping"], tmp_path / "rolled", source_folder=src, at=AT)
    rolled = {p.stem: json.loads(p.read_text()) for p in (tmp_path / "rolled").glob("*.json")}
    assert rolled["IF-syn-2"]["status"] == "done" and rolled["IF-syn-2"]["outcome"] == "dropped"
    assert "Dropped after cutover." in rolled["IF-syn-2"]["comments"][-1]["text"]
    post = [r for k, r in rolled.items() if k.startswith("IF-post-")]
    assert len(post) == 1 and post[0]["with"] == "mc" and post[0]["opened_by"] == "cos"
    why = " | ".join(u["why"] for u in back["unmappable"])
    assert "lead resolved after cutover: mc" in why and "holder codex" in why
    assert "delegated step" in why and "never promoted" in why
    for r in rolled.values():
        assert cos_contracts.errors("store-records", "in_flight", r) == [], r["item_id"]
    with pytest.raises(ValueError):
        reverse(svc, "tok-principal", read_rows(snap), out["mapping"], src, source_folder=src, at=AT)


def test_m03_the_snapshot_and_the_migrated_store_both_verify_before_any_cutover(tmp_path, legacy):
    src, snap = legacy
    (snap / "IF-syn-1.json").write_text(json.dumps(dict(ROWS[0], title="tampered"), sort_keys=True, indent=1))
    with pytest.raises(ValueError, match="changed after the snapshot"):
        read_rows(snap)
    shutil.rmtree(snap)
    snapshot_legacy(src, snap)
    svc, out, m = run(tmp_path, snap)
    head = svc.journal.head()
    shutil.copytree(tmp_path / "tasks", tmp_path / "backup")
    check = restore_check(tmp_path / "backup", doc_dirs=tuple(DIRS.values()), exported_heads=[head], harness=None)
    assert check["journal"] == "verified" and check["gaps"] == []


def test_owner_overrides_are_the_principals_only(tmp_path):
    svc = service(tmp_path)
    f = Flow(svc)
    f.source("SE-syn-0001", "Ask.")
    f.request("RQ-syn-0001", "SE-syn-0001", "Ask.", responder="cos")
    f.ok(cmd("request.set_state", "request", "RQ-syn-0001", {"state": "active", "reason": None}, expected=1), "tok-cos")
    answer = cmd("request.answer", "request", "RQ-syn-0001",
                 {"result_refs": [], "coverage": {"sources": [], "gaps": ["none"]}, "fence_generation": 0}, expected=2)
    assert svc.submit(answer, "tok-mc")["detail"] == "not_responder"
    assert svc.submit(dict(answer, command_id="cmd-own-ans"), "tok-principal")["outcome"] == "accepted"
    tid = f.promoted_task(n="0002")
    f.transition(tid, "working", evidence=[ref("harness", "p")])
    assert f.transition(tid, "waiting", token="tok-cos",
                        waiting={"reason": "input", "who_can_unblock": "principal"})["code"] == "not_authorized"
    assert f.transition(tid, "waiting", token="tok-principal",
                        waiting={"reason": "input", "who_can_unblock": "principal"})["outcome"] == "accepted"
