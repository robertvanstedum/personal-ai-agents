"""FF-19: MC's helper writes the daily job-health note on its own schedule (Robert, 12 Nov 2026: no note from him needed).
Against the real task service (the synthetic rig): the scheduled start is admitted only for a type the approved charter marks
as scheduled; the Apprentice works it with a stand-in model; MC accepts only a note whose items match the reports."""
from __future__ import annotations

import importlib.util
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.canonical_json import digest
from core.jobs import health, status
from services.helper_seats import spend, types as T
from services.helper_seats.worker import Worker
from services.partnership_tasks.charters import Charter, CharterRefused, Charters, _check
from tests.partnership.synth import Flow, cmd, service
from tests.partnership.test_helper_charters import GRANT, KIND
from tests.partnership.test_helper_worker import Client

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("helper_check_t", REPO / "scripts" / "jobs" / "helper_check.py")
hc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hc)

NOW = datetime(2026, 11, 14, 15, 15, tzinfo=timezone.utc)
SCHEDULED = dict(KIND, purpose="Daily job-health note", inputs=["date"], tools=["health.read"], per_task_budget_usd=0.02,
                 max_revisions=0, schedule="daily 09:15 America/Chicago")
CHARTER = Charter("worker-a", 1, "sha256:" + "a" * 64, frozenset({"mc"}), frozenset({"personal"}),
                  {"health-check": SCHEDULED, "repo-lookup": KIND}, 1.0)


def reports(folder):
    """ec2 with one failing job, the Mac quiet for 40 h, Dev fresh and green."""
    health.publish(folder, "health-ec2.json", {"schema_version": 1, "host": "ec2", "checked_at": status.iso(NOW - timedelta(minutes=8)),
                                               "worst": "red", "counts": {}, "items": [
        {"id": "backup-s3", "name": "Production backup, tier 2 S3", "host": "ec2", "light": "red", "code": "failed",
         "off_since": status.iso(NOW - timedelta(days=2))}]})
    health.publish(folder, "health-mac.json", {"schema_version": 1, "host": "mac", "checked_at": status.iso(NOW - timedelta(hours=40)),
                                               "worst": "green", "counts": {}, "items": []})
    health.publish(folder, "health-dev.json", {"schema_version": 1, "host": "dev", "checked_at": status.iso(NOW - timedelta(minutes=8)),
                                               "worst": "green", "counts": {}, "items": []})


@pytest.fixture
def rig(tmp_path):
    svc = service(tmp_path / "tasks")
    svc.charters = Charters([CHARTER])
    folder = tmp_path / "health"
    reports(folder)
    calls = []
    ledger = spend.Ledger(tmp_path / "spend", daily_cap=1.0)

    def ctx_for(task, kind, round_no):
        def ask(prompt, purpose):
            calls.append(prompt)
            return getattr(ctx_for, "reply", "needs action: Production backup S3 (ec2) failing 2 days.\nwatch: no report from the Mac for 40 h.")
        return T.Ctx(health_dir=folder, ask_model=ask, now=NOW)
    worker = Worker(Client(svc, "tok-worker"), "worker-a", CHARTER, environment="synthetic", ledger=ledger, ctx_for=ctx_for)
    return svc, Client(svc, "tok-mc"), Client(svc, "tok-platform"), worker, folder, calls, ctx_for


def run(rig, **kw):
    svc, mc, platform, worker, folder, *_ = rig
    return hc.run_once(mc, platform, CHARTER, environment="synthetic", health_dir=folder, now=NOW, wait_s=3,
                       sleep=lambda s: worker.pass_once(), seat="worker-a", **kw)


def test_the_helper_starts_on_schedule_drafts_and_mc_accepts(rig):
    svc, mc, *_ , calls, _ = rig
    note = run(rig)
    assert note["accepted_by"] == "mc" and note["drafted_by"] == "worker-a" and note["note_by"] == "model"
    assert note["note"].startswith("needs action: Production backup S3") and note["needs_action"] == 1
    task = mc.get("task", note["task_id"])
    assert task["charter_ref"]["type_id"] == "health-check"
    assert any(a["status"] == "current" for a in task["acceptances"]) and task["reviews"][0]["verdict"] == "approve"
    source = mc.get("source_event", mc.get("request", task["request_ref"])["source_event_id"])
    assert source["source"]["store"] == "schedule" and source["source"]["id"] == "health-check:2026-11-14"
    assert len(calls) == 1 and "Watchdog report from mac" in calls[0]


def test_a_rerun_the_same_day_is_the_same_task_and_no_second_model_call(rig):
    *_, calls, _ = rig
    first = run(rig)
    second = run(rig)
    assert first["task_id"] == second["task_id"] and second["accepted_by"] == "mc" and len(calls) == 1


def test_items_that_do_not_match_the_reports_are_not_accepted(rig):
    svc, mc, platform, worker, folder, calls, ctx_for = rig
    original = T.health_items

    def wrong(health_dir, now=None):
        items, rep, silent = original(health_dir, now)
        return items[1:], rep, silent                         # the helper "misses" one
    T.health_items = wrong
    try:
        hc.start(mc, platform, CHARTER, "2026-11-14", environment="synthetic", seat="worker-a")
        worker.pass_once()
    finally:
        T.health_items = original
    note = run(rig)
    assert note["accepted_by"] is None and "did not match" in note["why"]
    assert note["note_by"] == "deterministic" and "Production backup, tier 2 S3" in note["note"]


def test_a_model_failure_keeps_the_deterministic_words_and_is_still_accepted(rig):
    *_, ctx_for = rig

    ctx_for.reply = None
    note = run(rig)
    # reply None -> empty wording -> the type keeps its deterministic note; MC accepts the matching items
    assert note["accepted_by"] == "mc" and note["note_by"] == "deterministic"


def test_a_type_not_marked_scheduled_cannot_start_on_a_clock(rig):
    svc, mc, platform, *_ = rig
    plain = Charter("worker-a", 1, CHARTER.digest, CHARTER.leads, CHARTER.scopes, {"health-check": dict(KIND)}, 1.0)
    with pytest.raises(hc.NotStarted, match="not_chartered"):
        hc.start(mc, platform, plain, "2026-11-14", environment="synthetic", seat="worker-a")


def test_the_task_service_refuses_a_schedule_source_for_an_unscheduled_type(rig):
    svc, mc, platform, *_ = rig
    f = Flow(svc)
    sid = "SE-sched-x"
    assert svc.submit(cmd("source_event.record", "source_event", sid, {"source": {"store": "schedule", "id": "repo-lookup:2026-11-14",
                                                                                   "position": 1}, "content": "x", "correction_of": None}),
                      "tok-platform")["outcome"] == "accepted"
    terms = {"lead": "mc", "holder": "worker-a", "outcome": "x", "done_when": "{}", "return_window": None}
    proposed = {"outcome": "x", "scope": "personal", "recipients": ["principal"], "continuing": True, "task": terms}
    svc.submit(cmd("interpretation.record", "interpretation", "IN-h-0901", {"source_event_ids": [sid], "correction_event_ids": [], "proposed": proposed,
                                                                       "context_refs": [], "supersedes": None}), "tok-mc")
    svc.submit(cmd("interpretation.dispose", "interpretation", "IN-h-0901", {"disposition": "adopted", "authority": GRANT},
                   expected=f.rev("interpretation", "IN-h-0901")), "tok-mc")
    svc.submit(cmd("request.record", "request", "RQ-h-0901", {"source_event_id": sid, "intent_text": "x", "interpretation_ref": "IN-h-0901",
                                                          "responder": "mc"}), "tok-mc")
    for type_id in ("repo-lookup", "health-check"):          # unscheduled type; scheduled type but the record names another
        ref = {"seat": "worker-a", "version": 1, "digest": CHARTER.digest, "type_id": type_id}
        out = svc.submit(cmd("request.promote", "request", "RQ-h-0901", {"task_id": "TK-h-0901", "parent_ref": None, **terms, "charter_ref": ref},
                             expected=f.rev("request", "RQ-h-0901")), "tok-mc")
        assert out["detail"] == "not_the_owners_kept_note", (type_id, out)


def test_the_lead_cannot_forge_a_schedule_record(rig):
    svc, *_ = rig
    out = svc.submit(cmd("source_event.record", "source_event", "SE-forged", {"source": {"store": "schedule", "id": "health-check:2026-11-14",
                                                                                          "position": 1}, "content": "x", "correction_of": None}), "tok-mc")
    if out["outcome"] == "accepted":                         # MC may record events, but never as the platform
        assert svc.get("source_event", "SE-forged", "tok-mc")["producer"] != "platform"


# ---- charters ------------------------------------------------------------------------------------------------------

def test_a_scheduled_type_needs_words_and_no_effects():
    doc = json.loads((REPO / "config/partnership/charters/apprentice/v3.json").read_text())
    _check(doc, "apprentice@3")
    assert doc["types"]["health-check"]["tools"] == ["health.read"] and doc["types"]["health-check"]["effects"] == "none"
    bad = json.loads(json.dumps(doc))
    bad["types"]["health-check"]["effects"] = "internal"
    with pytest.raises(CharterRefused):
        _check(bad, "x")
    bad["types"]["health-check"].update(effects="none", schedule="")
    with pytest.raises(CharterRefused):
        _check(bad, "x")


def test_v3_ships_inert_until_approved_and_a_changed_approved_charter_still_refuses(tmp_path):
    root = tmp_path / "charters"
    shutil.copytree(REPO / "config/partnership/charters", root)
    approvals = {}
    for seat, version in (("apprentice", 1), ("apprentice", 2), ("ea", 1), ("ea", 2)):
        doc = json.loads((root / seat / f"v{version}.json").read_text())
        approvals[f"{seat}@{version}"] = {"digest": digest(doc), "approved_by": "robert", "ref": "test"}
    (tmp_path / "a.json").write_text(json.dumps(approvals))
    charters = Charters.load(root, tmp_path / "a.json")
    assert charters.get("apprentice").version == 2 and charters.get("apprentice", 3) is None     # helpers stay on
    v3 = json.loads((root / "apprentice/v3.json").read_text())
    approvals["apprentice@3"] = {"digest": digest(v3), "approved_by": "robert", "ref": "12 Nov note"}
    (tmp_path / "a.json").write_text(json.dumps(approvals))
    assert "health-check" in Charters.load(root, tmp_path / "a.json").get("apprentice").types
    approvals["apprentice@3"]["digest"] = "sha256:" + "0" * 64                                  # changed: refused, as before
    (tmp_path / "a.json").write_text(json.dumps(approvals))
    with pytest.raises(CharterRefused):
        Charters.load(root, tmp_path / "a.json")


def test_the_mc_block_shows_the_accepted_note(tmp_path):
    from minimoi_portal.guild_ui import jobs_health
    from core.jobs import registry
    folder = tmp_path / "health"
    reports(folder)
    health.publish(folder, "helper-note.json", {"schema_version": 1, "date": "2026-11-14", "written_at": status.iso(NOW),
                                                "accepted_by": "mc", "note": "needs action: backup S3 failing 2 days."})
    text = jobs_health.block("robert", environ={"MINIMOI_SUPERVISION_OWNER": "robert"}, now=NOW, root=str(folder),
                             registry_loader=registry.load)
    assert "Apprentice's daily note (2026-11-14; drafted by the Apprentice, accepted by MC):" in text
    assert "needs action: backup S3 failing 2 days." in text
    later = jobs_health.block("robert", environ={"MINIMOI_SUPERVISION_OWNER": "robert"}, now=NOW + timedelta(days=2),
                              root=str(folder), registry_loader=registry.load)
    assert "Apprentice's daily note" not in later                                                 # a stale note is left out
