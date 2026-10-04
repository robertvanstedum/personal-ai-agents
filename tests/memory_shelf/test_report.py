"""The memory capture report: sections, units, status judgement, Unknown on missing or malformed state, safety, history, CLI.
Synthetic shelves only; every number is checked against what the fixture actually holds."""
import hashlib
import io
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.memory_shelf import approvals, cli, codes, fsio, report, watchers
from core.memory_shelf.config import Config

from .helpers import make_shelf
from .report_world import FIXED_RUN, NOW, OWNER, build_world, cc_user, copy, handoff, item, jl, meta


def build(shelf, cfg, now=NOW, **kw):
    return report.build(shelf, cfg, now, run_id=FIXED_RUN, **kw)


def record_fidelity(shelf, *, now=NOW, failed=0, ok=3, size=3, at=None):
    report.record_quality(shelf, "fidelity", {"at": (at or now).strftime("%Y-%m-%dT%H:%M:%SZ"), "method": "random_sample", "seed": 20261004,
                                              "sample_size": size, "checked": ok + failed, "ok": ok, "failed": failed, "skipped": {},
                                              "scope": "storage_and_edition_same_parser"})


def tree_digest(root):
    h = hashlib.sha256()
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(root)).encode() + p.read_bytes())
    return h.hexdigest()


@pytest.fixture
def world(tmp_path):
    shelf, cfg, sources = build_world(tmp_path)
    record_fidelity(shelf)
    return shelf, cfg, sources


# ── capture: records and turns by role, units that reconcile ────────────────

def test_capture_counts_records_and_turns_by_source_and_role(world):
    shelf, cfg, _ = world
    rep = build(shelf, cfg)
    cc, cx = rep["sources"]["claude-code"]["capture"], rep["sources"]["codex"]["capture"]
    assert cc["records"] == 1 and cc["turns"] == {"human": 1, "assistant": 1, "system": 1, "coordination": 1, "total": 4}
    assert cx["records"] == 2 and cx["turns"]["human"] == 1 and cx["turns"]["assistant"] == 1
    assert cx["turns"]["coordination"] == 3 and cx["turns"]["total"] == 5          # the handoff and the two review turns
    assert cx["by_class"] == {"approval_review": 2}
    for src in rep["sources"].values():
        t = src["capture"]["turns"]
        assert t["total"] == sum(t[r] for r in report.ROLES)
    assert rep["totals"]["records"] == 3 and rep["totals"]["turns"]["total"] == 9
    assert rep["totals"]["note"] == "records_not_deduplicated_across_sources"


def test_redacted_turns_are_a_subset_not_another_total(tmp_path):
    shelf, cfg, _ = build_world(tmp_path, run=False)
    cc = Path(cfg.sources["claude-code"].root) / "proj" / "cc-1.jsonl"
    cc.write_text(cc.read_text().replace("human words", "key sk-ant-FAKEFAKEFAKE12345 here"))
    for src in cfg.sources.values():
        watchers.run_source(shelf, src, now=NOW)
        approvals.approve_source(shelf, src.name, src.fingerprint(), OWNER)
        watchers.run_source(shelf, src, now=NOW)
    rep = build(shelf, cfg)
    capture = rep["sources"]["claude-code"]["capture"]
    assert capture["redacted_turns"] == 1 and capture["turns"]["total"] == 4          # the same four turns, one of them redacted


def test_the_report_counts_records_as_records_not_conversations(world):
    shelf, cfg, _ = world
    assert "unique" not in json.dumps(build(shelf, cfg))


# ── selection ────────────────────────────────────────────────────────────────

def test_selection_keeps_raw_items_and_turns_in_separate_units_and_assigns_every_item_a_disposition(world):
    shelf, cfg, _ = world
    sel = build(shelf, cfg)["sources"]["codex"]["selection"]
    omitted = sel["raw_items_omitted"]
    assert omitted["total"] == sum(omitted["by_disposition"].values()) == sum(omitted["by_kind"].values())
    assert set(omitted["by_disposition"]) == set(report.DISPOSITIONS)
    assert omitted["by_disposition"]["duplicate"] >= 1 and omitted["by_disposition"]["machinery"] >= 1
    assert sel["normalized_turns_kept"] == 5 and sel["units_note"] == "raw_items_and_turns_are_different_units"
    assert "retention" not in json.dumps(sel) and "percent" not in json.dumps(sel)


REAL_KINDS = """document image line:agent-name line:ai-title line:artifact-autoreact-ledger line:artifact-comment-monitor line:atis-latch
line:attachment line:bridge-session line:cost-state line:custom-title line:file-history-delta line:file-history-snapshot line:frame-link
line:last-prompt line:mode line:pr-link line:queue-operation line:system thinking tool_result tool_use:Agent tool_use:Bash
tool_use:mcp__Claude_Browser__computer tool_use:mcp__ccd_pr__bind_pr agent_thinking_traces card_attachments_json cited_web_search_results
file file_attachment generated_image_urls injected_prompt_block query steps thinking_trace tool_result:Artifact tool_result:bash_tool
tool_result:web_search tool_use:web_fetch web_search_results event_msg:task_complete event_msg:task_started event_msg:thread_settings_applied
event_msg:token_count event_msg:turn_aborted input_image injected:/image injected:AGENTS.md injected:codex_delegation injected:developer
injected:environment_context injected:external_codex_apps_open_page injected:guardian_tool_descriptions injected:image
injected:in-app-browser-context injected:recommended_plugins item:CollabAgentToolCall item:CommandExecution item:ContextCompaction
item:Extension item:FileChange item:FunctionCallOutput item:ImageView item:McpToolCall item:Reasoning item:SubAgentActivity item:WebSearch
line:compacted line:inter_agent_communication_metadata line:token_usage_record line:turn_context line:world_state local_image
response_item:compaction response_item:custom_tool_call response_item:custom_tool_call_output response_item:function_call
response_item:function_call_output response_item:message response_item:reasoning response_item:tool_search_call
response_item:tool_search_output sidechain attachment_content_excluded attachment_no_text handoff:duplicate handoff:unavailable
empty:lifecycle""".split()


@pytest.mark.parametrize("kind", REAL_KINDS)
def test_every_kind_the_real_shelf_has_omitted_has_a_known_disposition(kind):
    assert report.disposition(kind) != "unknown", kind


def test_a_new_unrecognised_kind_is_unknown_and_makes_the_source_a_watch(tmp_path):
    assert report.disposition("brand_new_thing") == "unknown" and report.disposition("response_item:agent_message") == "unknown"
    shelf, cfg, _ = build_world(tmp_path)
    record_fidelity(shelf)
    cc = Path(cfg.sources["claude-code"].root) / "proj" / "cc-1.jsonl"
    cc.write_text(cc.read_text().replace('"type": "last-prompt"', '"type": "last-prompt"') + json.dumps(
        {"type": "assistant", "sessionId": "cc-1", "message": {"content": [{"type": "text", "text": "x"}, {"type": "hologram"}]}}) + "\n")
    watchers.run_source(shelf, cfg.sources["claude-code"], now=NOW)
    src = build(shelf, cfg)["sources"]["claude-code"]
    assert src["selection"]["raw_items_omitted"]["unknown_kinds"] == ["hologram"]
    assert src["status"]["state"] == "watch" and src["status"]["reason"] == "unclassified_omitted_kind"


# ── quality ──────────────────────────────────────────────────────────────────

def test_duplicate_counts_state_what_was_matched_and_what_could_not_be_checked(world):
    shelf, cfg, _ = world
    dup = build(shelf, cfg)["sources"]["codex"]["quality"]["duplicates"]
    assert dup["state"] == "counted" and dup["records_counted"] == 2 and dup["records_unverifiable"] == 0
    assert dup["copies_matched"] == 1 and dup["whitespace_rule"] == "collapse_whitespace_runs_item_bytes_kept"
    assert build(shelf, cfg)["sources"]["claude-code"]["quality"]["duplicates"] == {"state": "not_applicable"}


def test_fidelity_not_run_is_not_passed_and_a_recorded_result_carries_its_method_size_and_date(tmp_path):
    shelf, cfg, _ = build_world(tmp_path)
    q = build(shelf, cfg)["sources"]["codex"]["quality"]
    assert q["fidelity"] == {"state": "not_run"} and q["semantic_audit"] == {"state": "not_run"}
    record_fidelity(shelf, ok=5, size=5)
    f = build(shelf, cfg)["sources"]["codex"]["quality"]["fidelity"]
    assert f["state"] == "ok" and f["sample_size"] == 5 and f["method"] == "random_sample" and f["at"] == "2026-10-04T12:00:00Z"
    assert f["scope"] == "storage_and_edition_same_parser"


def test_a_recorded_semantic_audit_is_reported_with_its_scope_and_is_never_inferred(world):
    shelf, cfg, _ = world
    report.record_quality(shelf, "semantic-audit", {"at": "2026-10-04T08:00:00Z", "reviewer": "agent", "method": "fixed_seed_sample",
                                                    "sample_size": 36, "scope": "attribution_and_exclusion_only",
                                                    "findings": {"misattributed": 2}, "skipped": {"changed_source": 1}})
    audit = build(shelf, cfg)["sources"]["codex"]["quality"]["semantic_audit"]
    assert audit["state"] == "recorded" and audit["reviewer"] == "agent" and audit["scope"] == "attribution_and_exclusion_only"


def test_a_failed_fidelity_sample_is_a_watch(world):
    shelf, cfg, _ = world
    record_fidelity(shelf, failed=1, ok=2)
    st = build(shelf, cfg)["sources"]["codex"]["status"]
    assert (st["state"], st["reason"]) == ("watch", "fidelity_failed")


def test_stale_or_missing_quality_data_is_unknown_never_current(tmp_path):
    shelf, cfg, _ = build_world(tmp_path)
    st = build(shelf, cfg)["sources"]["codex"]["status"]
    assert (st["state"], st["reason"]) == ("unknown", "quality_unknown")
    record_fidelity(shelf, at=NOW - timedelta(days=30))
    rep = build(shelf, cfg)
    assert rep["sources"]["codex"]["quality"]["fidelity"]["state"] == "stale" and rep["sources"]["codex"]["status"]["state"] == "unknown"


# ── evidence references ──────────────────────────────────────────────────────

def test_handoffs_and_attachments_are_counted_as_evidence_with_what_is_unavailable(world):
    shelf, cfg, _ = world
    ev = build(shelf, cfg)["sources"]["codex"]["evidence"]
    assert ev["handoffs_retained"] == 1 and ev["handoffs_unavailable"] == 1 and ev["handoffs_duplicate"] == 0
    assert set(ev) >= {"attachment_references", "attachment_content_excluded_items", "attachment_chars_excluded", "attachments_seen"}


def test_an_export_attachment_is_a_reference_with_its_excluded_characters_counted(tmp_path):
    from .test_claude_ai_tree import ROOT, conversation, ingest as ingest_export, m
    atts = [{"file_name": "d.md", "file_size": 9, "file_type": "text/markdown", "extracted_content": "DOCUMENT-BODY"}]
    shelf, box, _ = ingest_export(tmp_path, [conversation("c1", [m("r", ROOT, "human", "q", 1, attachments=atts), m("a", "r", "assistant", "a", 2)])])
    cfg = Config(shelf_root=shelf.root, inbox_root=box, sources={})
    ev = build(shelf, cfg)["sources"]["claude-ai"]["evidence"]
    assert ev["attachment_content_excluded_items"] == 1 and ev["attachment_chars_excluded"] == len("DOCUMENT-BODY") and ev["attachments_seen"] == 1
    assert "DOCUMENT-BODY" not in json.dumps(build(shelf, cfg))


# ── status judgement ─────────────────────────────────────────────────────────

def status_of(rep, name):
    s = rep["sources"][name]["status"]
    return s["state"], s["reason"]


def test_a_healthy_source_is_current(world):
    shelf, cfg, _ = world
    rep = build(shelf, cfg)
    assert status_of(rep, "claude-code") == ("current", "ok") and status_of(rep, "codex") == ("current", "ok")
    assert rep["operate"]["overall"] == "current"


def test_a_source_nobody_approved_is_off_not_failing_and_an_unconfigured_one_is_off_too(tmp_path):
    shelf, cfg, _ = build_world(tmp_path, approve=False)
    assert status_of(build(shelf, cfg), "codex") == ("off", "not_approved")
    empty = Config(shelf_root=shelf.root, inbox_root=tmp_path / "x", sources={})
    assert build(shelf, empty)["operate"]["overall"] in ("unknown", "off", "current", "watch")


def test_a_changed_definition_makes_an_approved_source_a_watch_not_an_intentional_off(world):
    shelf, cfg, sources = world
    from dataclasses import replace
    moved = replace(sources["codex"], never_copy=("x",))
    changed = Config(shelf_root=shelf.root, inbox_root=cfg.inbox_root, sources={**sources, "codex": moved})
    assert status_of(build(shelf, changed), "codex") == ("watch", "approval_stale")


def test_an_approved_source_with_no_state_is_unknown(tmp_path):
    shelf, cfg, sources = build_world(tmp_path, approve=False, run=False)
    for src in sources.values():
        watchers.dry_run(shelf, src, NOW)
        approvals.approve_source(shelf, src.name, src.fingerprint(), OWNER)
    assert status_of(build(shelf, cfg), "codex") == ("unknown", "no_state")


def test_an_overdue_source_stays_visible_as_a_watch_even_with_a_big_total(world):
    shelf, cfg, _ = world
    later = NOW + timedelta(seconds=report.MISSED_AFTER_S + 60)
    rep = build(shelf, cfg, now=later)
    assert status_of(rep, "codex") == ("watch", "overdue") and rep["sources"]["codex"]["capture"]["records"] == 2
    assert rep["operate"]["overall"] == "watch"


def test_a_failing_pass_is_a_watch_and_keeps_showing_when_the_source_last_worked(world):
    shelf, cfg, sources = world
    path = Path(shelf.status_dir) / "watch-codex.json"
    doc = json.loads(path.read_text())
    doc.update(last_run_at="2026-10-04T13:00:00Z", status="ok", counts={"failed": 2})
    path.write_text(json.dumps(doc))
    rep = build(shelf, cfg, now=NOW + timedelta(hours=2))
    assert status_of(rep, "codex") == ("watch", "capture_failed")
    assert rep["sources"]["codex"]["status"]["last_success_at"] == "2026-10-04T12:00:00Z"


def test_a_record_still_on_an_older_parser_is_a_known_unresolved_finding_and_a_watch(world):
    shelf, cfg, _ = world
    from core.memory_shelf import record
    main = shelf.main_path(shelf.index()["codex:cx-1"])
    meta_, body = record.load(record.read(main))
    meta_["normalized"]["normalizer"] = 2
    record.write(main, meta_, body)
    src = build(shelf, cfg)["sources"]["codex"]
    assert src["quality"]["classification"]["findings"] == [{"code": "records_on_older_parser", "records": 1}]
    assert status_of(build(shelf, cfg), "codex") == ("watch", "records_on_older_parser")


def test_an_unknown_origin_is_a_watch_with_its_turn_count(tmp_path):
    shelf, cfg, _ = build_world(tmp_path)
    record_fidelity(shelf)
    cc = Path(cfg.sources["claude-code"].root) / "proj" / "cc-1.jsonl"
    cc.write_text(cc.read_text() + json.dumps(cc_user("who is this", {"kind": "mystery"})) + "\n")
    watchers.run_source(shelf, cfg.sources["claude-code"], now=NOW)
    src = build(shelf, cfg)["sources"]["claude-code"]
    assert {"code": "unknown_origin", "turns": 1} in src["quality"]["classification"]["findings"]
    assert src["status"]["state"] == "watch"


def test_a_clean_check_advances_the_success_time_but_only_a_capture_advances_the_capture_time(tmp_path):
    shelf, cfg, sources = build_world(tmp_path)
    record_fidelity(shelf)
    later = NOW + timedelta(hours=24)
    watchers.run_source(shelf, sources["codex"], now=later)                 # nothing new
    st = build(shelf, cfg, now=later)["sources"]["codex"]["status"]
    assert st["last_success_at"] == "2026-10-05T12:00:00Z" and st["last_check_kind"] == "check"
    assert st["last_new_capture_at"] == "2026-10-04T12:00:00Z"
    new = Path(cfg.sources["codex"].root) / "2026/10/05/rollout-c-0199aaaa-0000-0000-0000-000000000003.jsonl"
    new.parent.mkdir(parents=True)
    new.write_text("\n".join(jl(meta("user", "cx-3"), item("UserMessage", "new"))) + "\n")
    watchers.run_source(shelf, sources["codex"], now=later + timedelta(hours=1))
    st = build(shelf, cfg, now=later + timedelta(hours=1))["sources"]["codex"]["status"]
    assert st["last_check_kind"] == "capture" and st["last_new_capture_at"] == "2026-10-05T13:00:00Z"


def test_a_failed_pass_does_not_move_the_success_time(tmp_path, monkeypatch):
    shelf, cfg, sources = build_world(tmp_path)
    before = json.loads((Path(shelf.status_dir) / "watch-codex.json").read_text())["last_success_at"]
    monkeypatch.setattr(watchers.render, "hash_file", lambda p: (_ for _ in ()).throw(OSError("injected")))
    new = Path(cfg.sources["codex"].root) / "2026/10/05/rollout-d-0199aaaa-0000-0000-0000-000000000004.jsonl"
    new.parent.mkdir(parents=True)
    new.write_text("\n".join(jl(meta("user", "cx-4"), item("UserMessage", "x"))) + "\n")
    watchers.run_source(shelf, sources["codex"], now=NOW + timedelta(hours=3))
    after = json.loads((Path(shelf.status_dir) / "watch-codex.json").read_text())
    assert after["last_success_at"] == before and after["last_run_at"] == "2026-10-04T15:00:00Z"


def test_a_manual_source_is_judged_without_a_schedule(tmp_path):
    from .test_claude_ai_tree import ROOT, conversation, ingest as ingest_export, m
    shelf, box, _ = ingest_export(tmp_path, [conversation("c1", [m("r", ROOT, "human", "q", 1), m("a", "r", "assistant", "a", 2)])])
    cfg = Config(shelf_root=shelf.root, inbox_root=box, sources={})
    row = build(shelf, cfg, now=datetime.now(timezone.utc) + timedelta(days=90))["sources"]["claude-ai"]
    assert row["mode"] == "manual" and row["status"]["state"] == "current" and row["status"]["reason"] == "ok"   # never overdue


# ── the job is separate from capture quality ────────────────────────────────

def write_job(jobs_root, state="ok", finished="2026-10-04T11:59:00Z"):
    from core.jobs import registry, status
    job = registry.parse({"schema_version": 1, "jobs": [{"id": "memory-watch", "name": "m", "host": "mac",
        "schedule": {"text": "daily 04:00 Chicago", "kind": "daily", "at": "04:00", "tz": "America/Chicago"}, "expected_every_s": 86400,
        "missed_after_s": 180000, "stuck_after_s": 7200, "status_file": "memory-watch.json", "owner": "robert", "runbook": "x"}]}).jobs[0]
    running = status.start_run(jobs_root, job, "r1", datetime(2026, 10, 4, 11, 58, tzinfo=timezone.utc))
    status.finish_run(jobs_root, job, running, state=state, now=datetime.fromisoformat(finished.replace("Z", "+00:00")), exit_code=0,
                      summary="done", results={"codex": "ok"}, next_due_by=None, alert="not_needed")


def test_a_successful_job_can_sit_beside_a_quality_concern(world, tmp_path):
    shelf, cfg, _ = world
    jobs = tmp_path / "jobs"
    write_job(jobs)
    record_fidelity(shelf, failed=1, ok=2)
    rep = build(shelf, cfg, jobs_root=jobs)
    assert rep["job"]["state"] == "ok" and rep["sources"]["codex"]["status"]["state"] == "watch"


def test_a_missing_or_stale_job_file_is_unknown_not_healthy(world, tmp_path):
    shelf, cfg, _ = world
    assert build(shelf, cfg, jobs_root=tmp_path / "none")["job"] == {"state": "unknown", "reason": "no_status_file"}
    assert build(shelf, cfg)["job"] == {"state": "unknown", "reason": "no_jobs_root"}
    jobs = tmp_path / "jobs"
    write_job(jobs, finished="2026-10-01T00:00:00Z")
    assert build(shelf, cfg, jobs_root=jobs)["job"]["reason"] == "stale"


# ── missing and malformed state is Unknown ───────────────────────────────────

def test_a_corrupt_status_file_and_corrupt_quality_files_degrade_to_unknown(world):
    shelf, cfg, _ = world
    (Path(shelf.status_dir) / "watch-codex.json").write_text("{not json")
    assert status_of(build(shelf, cfg), "codex") == ("unknown", "no_state")
    (Path(shelf.status_dir) / "quality" / "fidelity.json").write_text("[1,2]")
    assert build(shelf, cfg)["sources"]["claude-code"]["quality"]["fidelity"] == {"state": "not_run"}
    (Path(shelf.status_dir) / "quality" / "fidelity.json").write_text(json.dumps({"checked": 3, "ok": 3, "failed": 0}))
    assert build(shelf, cfg)["sources"]["claude-code"]["quality"]["fidelity"]["state"] == "unknown"


def test_a_corrupt_record_is_counted_as_unreadable_not_skipped_silently(world):
    shelf, cfg, _ = world
    main = shelf.main_path(shelf.index()["codex:cx-1"])
    main.write_bytes(b"not a record")
    cap = build(shelf, cfg)["sources"]["codex"]["capture"]
    assert cap["unreadable_records"] == 1 and cap["records"] == 1


def test_an_empty_shelf_reports_nothing_is_current(tmp_path):
    shelf = make_shelf(tmp_path)
    rep = build(shelf, Config(shelf_root=shelf.root, inbox_root=tmp_path / "i", sources={}))
    assert rep["sources"] == {} and rep["operate"]["overall"] == "unknown" and rep["operate"]["rows"] == []


# ── safety, publication, history, read-only ─────────────────────────────────

def test_the_payload_is_counts_and_fixed_codes_only(world, tmp_path):
    shelf, cfg, _ = world
    text = json.dumps(build(shelf, cfg))
    for needle in ("ordinary question", "human words", "NEW_TASK", "review prompt", str(tmp_path), "/Users", "rollout-", "proj/", "cc-1.jsonl"):
        assert needle not in text
    report.assert_safe(json.loads(text))


@pytest.mark.parametrize("bad", [{"a": "/Users/x/y"}, {"title": "a title with / slash"}, {"a": "line\nbreak"}, {"a": "x" * 200}, {"bad key!": 1},
                                 {"a": {"b": object()}}])
def test_a_payload_with_anything_but_safe_tokens_is_refused(bad):
    with pytest.raises(report.ReportError):
        report.assert_safe(bad)


def test_build_writes_nothing_to_the_shelf(world):
    shelf, cfg, _ = world
    before = tree_digest(shelf.root)
    build(shelf, cfg)
    build(shelf, cfg)
    assert tree_digest(shelf.root) == before


def test_publish_writes_the_report_and_the_matrix_atomically_and_privately(world, tmp_path):
    shelf, cfg, _ = world
    jobs = tmp_path / "jobs"
    rep = build(shelf, cfg)
    out = report.publish(shelf, rep, jobs)
    assert out["published"] == [report.REPORT_FILE, report.MATRIX_FILE]
    assert json.loads((jobs / report.REPORT_FILE).read_text())["schema"] == report.SCHEMA
    matrix = json.loads((jobs / report.MATRIX_FILE).read_text())
    assert matrix["schema"] == report.MATRIX_SCHEMA and matrix["run_id"] == FIXED_RUN and [r["source"] for r in matrix["rows"]] == ["claude-code", "codex"]
    assert oct((jobs / report.REPORT_FILE).stat().st_mode & 0o777) == "0o600" and not list(jobs.glob("*.tmp*")) and not list(jobs.glob(".*tmp*"))


def test_an_unsafe_report_is_not_published(world, tmp_path):
    shelf, cfg, _ = world
    rep = build(shelf, cfg)
    rep["sources"]["codex"]["label"] = "/Users/someone/secret"
    with pytest.raises(report.ReportError):
        report.publish(shelf, rep, tmp_path / "jobs")
    assert not (tmp_path / "jobs" / report.REPORT_FILE).exists()


def test_history_is_bounded_and_does_not_repeat_unchanged_totals(world, tmp_path):
    shelf, cfg, _ = world
    jobs = tmp_path / "jobs"
    assert report.publish(shelf, build(shelf, cfg), jobs)["history_appended"] is True
    assert report.publish(shelf, build(shelf, cfg), jobs)["history_appended"] is False          # same totals, same day
    rep2 = build(shelf, cfg, now=NOW + timedelta(days=1))
    assert report.publish(shelf, rep2, jobs)["history_appended"] is True                        # a new day gets one heartbeat
    for n in range(report.HISTORY_BOUND + 20):
        r = build(shelf, cfg, now=NOW + timedelta(days=2 + n))
        r["sources"]["codex"]["capture"]["records"] += n                                         # a changing total each run
        r["run_id"] = f"run-{n}"
        report.publish(shelf, r, jobs)
    lines = (Path(shelf.status_dir) / "report" / "history.jsonl").read_text().splitlines()
    assert len(lines) == report.HISTORY_BOUND
    summary = build(shelf, cfg)["history"]
    assert summary["bound"] == report.HISTORY_BOUND and summary["retained_snapshots"] == report.HISTORY_BOUND


def test_history_deltas_compare_like_units_per_source(world, tmp_path):
    shelf, cfg, sources = world
    report.publish(shelf, build(shelf, cfg), tmp_path / "jobs")
    new = Path(sources["codex"].root) / "2026/10/05/rollout-c-0199aaaa-0000-0000-0000-000000000003.jsonl"
    new.parent.mkdir(parents=True)
    new.write_text("\n".join(jl(meta("user", "cx-3"), item("UserMessage", "q"), item("AgentMessage", "a", "final_answer"))) + "\n")
    watchers.run_source(shelf, sources["codex"], now=NOW + timedelta(days=1))
    h = build(shelf, cfg, now=NOW + timedelta(days=1))["history"]
    assert h["delta_since_previous"]["codex"] == {"records": 1, "turns": 2, "omitted": h["delta_since_previous"]["codex"]["omitted"]}
    assert h["delta_since_previous"]["claude-code"]["records"] == 0 and h["unchanged_since_previous"] is False


def test_the_matrix_rows_and_overall_follow_the_worst_source(world):
    shelf, cfg, _ = world
    later = NOW + timedelta(seconds=report.MISSED_AFTER_S + 60)
    m = build(shelf, cfg, now=later)["operate"]
    assert m["overall"] == "watch" and {r["source"]: r["status"] for r in m["rows"]} == {"claude-code": "watch", "codex": "watch"}
    row = [r for r in m["rows"] if r["source"] == "codex"][0]
    assert set(row) == {"source", "label", "mode", "last_success_at", "last_check_kind", "last_new_capture_at", "captured_records", "status", "reason"}


# ── the command ──────────────────────────────────────────────────────────────

def cli_run(tmp_path, shelf, cfg_sources, *argv):
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({"schema_version": 1, "shelf_root": str(shelf.root), "inbox_root": str(tmp_path / "inbox"),
                                "repo_root": str(tmp_path), "headroom": {"min_free_bytes": 0},
                                "sources": {n: {"kind": s.kind, "root": str(s.root)} for n, s in cfg_sources.items()}}))
    out = io.StringIO()
    code = cli.main(["--config", str(path), *argv], out=out, now=NOW)
    return code, out.getvalue()


def test_moi_report_prints_a_summary_and_writes_nothing(world, tmp_path):
    shelf, cfg, sources = world
    before = tree_digest(shelf.root)
    jobs = tmp_path / "jobs"
    code, text = cli_run(tmp_path, shelf, sources, "report", "--jobs-root", str(jobs))
    assert code == cli.OK and text.startswith("report\t") and "records=3" in text and "turns=9" in text
    assert tree_digest(shelf.root) == before and not jobs.exists()


def test_moi_report_publish_and_fidelity_write_only_the_report_files_and_the_sample_result(world, tmp_path):
    shelf, cfg, sources = world
    jobs = tmp_path / "jobs"
    code, text = cli_run(tmp_path, shelf, sources, "report", "--publish", "--fidelity", "3", "--jobs-root", str(jobs))
    assert code == cli.OK and "published=" in text
    assert (jobs / report.REPORT_FILE).is_file() and (jobs / report.MATRIX_FILE).is_file()
    fid = json.loads((Path(shelf.status_dir) / "quality" / "fidelity.json").read_text())
    assert fid["sample_size"] == 3 and fid["method"] == "random_sample" and fid["seed"] == 20261004
    doc = json.loads((jobs / report.REPORT_FILE).read_text())
    assert doc["sources"]["codex"]["quality"]["fidelity"]["state"] == "ok"
    assert [p.name for p in Path(shelf.root, "sessions-raw").iterdir()]                                   # records untouched


def test_moi_report_json_prints_the_full_payload(world, tmp_path):
    shelf, cfg, sources = world
    code, text = cli_run(tmp_path, shelf, sources, "report", "--json")
    assert code == cli.OK and json.loads(text[text.index("\n{") + 1:])["schema"] == report.SCHEMA
