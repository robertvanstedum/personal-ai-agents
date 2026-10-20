"""P-G7: learning count snapshot consumer and the brief's learning line (standing §7.1, P-15)."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from core.mc_standing import learning_view as lv, render
from core.mc_standing.spool import Spool
from tests.mc_standing.test_sources_and_brief import _obs, make_watch
from tests.mc_standing.test_spool_findings_runner import NOW, FakeIngress, _status, run

PRINCIPAL = "owner:robert"


def snap(**over):
    doc = {"schema": lv.SCHEMA, "snapshot_id": "snap-20261021-0440", "principal": PRINCIPAL,
           "environment": "prod", "as_of": "2026-10-21T09:40:00Z", "coverage": "complete",
           "adopted_window_days": 7,
           "counts": {"pending_suggestions": 2, "adopted_in_window": 1,
                      "repeated_corrections_confirmed": 1, "repeated_corrections_provisional": 0},
           "site_labels": [{"kind": "repeated_correction", "label": "website, not Telegram"}]}
    doc.update(over)
    return doc


def view(doc=None, **kw):
    return lv.read_view(lambda: doc if doc is not None else snap(), principal=PRINCIPAL,
                        environment="prod", now=NOW, **kw)


def zero_counts():
    return {k: 0 for k in lv.COUNT_KEYS}


# ── validation ──────────────────────────────────────────────────────────────

def test_valid_snapshot_keeps_identity_as_of_and_coverage():
    v = view()
    assert v.known and v.snapshot_id == "snap-20261021-0440" and v.as_of == "2026-10-21T09:40:00Z"
    assert v.record()["counts"]["pending_suggestions"] == 2 and v.record()["coverage"] == "complete"
    assert "site_labels" not in v.record()


@pytest.mark.parametrize("doc,status,reason", [
    (snap(schema="mc-learning-count-snapshot/2"), "invalid", "schema_unknown"),
    (snap(statement_text="never accepted"), "invalid", "unexpected_field"),
    (snap(counts={"pending_suggestions": 1}), "invalid", "counts_invalid"),
    (snap(counts=dict(zero_counts(), pending_suggestions=-1)), "invalid", "count_invalid"),
    (snap(counts=dict(zero_counts(), pending_suggestions=True)), "invalid", "count_invalid"),
    (snap(as_of="2026-10-21T09:40:00+00:00"), "invalid", "as_of_invalid"),
    (snap(as_of="2026-10-21T11:40:00Z"), "invalid", "as_of_in_future"),
    (snap(coverage="mostly"), "invalid", "coverage_invalid"),
    (snap(site_labels=[{"kind": "repeated_correction", "label": "x" * 41}]), "invalid", "label_invalid"),
    (snap(site_labels=[{"kind": "repeated_correction", "label": "<script>"}]), "invalid", "label_invalid"),
    (snap(site_labels=[{"kind": "statement", "label": "ok"}]), "invalid", "label_invalid"),
    (snap(principal="owner:someone-else"), "mismatch", "principal_or_environment_mismatch"),
    (snap(environment="dev"), "mismatch", "principal_or_environment_mismatch"),
    (snap(as_of="2026-10-19T09:40:00Z"), "stale", "snapshot_stale"),
    (snap(coverage="unavailable"), "unavailable", "publisher_coverage_unavailable"),
    (b"{not json", "invalid", "snapshot_not_json"),
    (b" " * (lv.MAX_BYTES + 1), "invalid", "snapshot_too_large"),
])
def test_bad_snapshots_are_unknown_not_zero(doc, status, reason):
    v = view(doc)
    assert (v.status, v.reason) == (status, reason)
    assert not v.counts                                   # nothing usable leaks through
    assert lv.learning_line(v).startswith("Learning counts unavailable")


def test_fetch_failure_or_missing_snapshot_never_raises():
    def boom():
        raise ConnectionError("harness down")
    assert lv.read_view(boom, principal=PRINCIPAL, environment="prod", now=NOW).reason == "snapshot_fetch_failed"
    assert lv.read_view(lambda: None, principal=PRINCIPAL, environment="prod", now=NOW).reason == "snapshot_missing"


# ── rendering ───────────────────────────────────────────────────────────────

def test_zero_counts_with_full_coverage_render_nothing():
    assert lv.learning_line(view(snap(counts=zero_counts(), site_labels=[]))) is None


def test_zero_counts_with_partial_coverage_are_not_called_zero():
    line = lv.learning_line(view(snap(counts=zero_counts(), site_labels=[], coverage="partial")))
    assert line == "Learning: nothing new in the part checked (coverage partial)."


def test_site_line_shows_permitted_labels_and_separates_provisional_repeats():
    v = view(snap(counts=dict(zero_counts(), pending_suggestions=2, adopted_in_window=1,
                              repeated_corrections_confirmed=1, repeated_corrections_provisional=2)))
    line = lv.learning_line(v, destination="site", labels_permitted=True)
    assert line == ("Learning: 2 learning suggestions pending · 1 practice adopted this week · "
                    "1 repeated correction ('website, not Telegram') · 2 possible repeated corrections.")


def test_labels_hidden_unless_permitted_and_never_in_telegram():
    v = view()
    assert "website" not in lv.learning_line(v, destination="site", labels_permitted=False)
    telegram = lv.learning_line(v, destination="telegram", labels_permitted=True)
    assert "website" not in telegram and telegram.startswith("Learning: 2 learning suggestions pending")


def test_stale_line_names_the_last_snapshot_time():
    assert lv.learning_line(view(snap(as_of="2026-10-19T09:40:00Z"))) == \
        "Learning counts unavailable (last snapshot 2026-10-19T09:40:00Z)."


def test_partial_coverage_and_window_are_stated():
    v = view(snap(coverage="partial", adopted_window_days=14))
    assert lv.learning_line(v).endswith("1 practice adopted in the last 14 days · 1 repeated correction (coverage partial).")


def test_brief_without_learning_configured_has_no_learning_line():
    ws = [make_watch()]
    text = render.render_brief([_obs(ws[0], "healthy", "job_ok")], ws, local_date=date(2026, 10, 21),
                               environment="prod")
    assert "Learning" not in text


# ── runner integration ─────────────────────────────────────────────────────

def _payload(spool):
    names = [n for n in spool.names("delivery") if n.endswith(".brief")]
    return spool.read("delivery", names[0])


def test_brief_carries_learning_line_and_snapshot_evidence(tmp_path):
    _status(tmp_path / "src")
    spool, res = run(tmp_path, FakeIngress(), learning_source=lambda: json.dumps(snap()),
                     learning_principal=PRINCIPAL)
    assert res["state"] == "delivered"
    payload = _payload(spool)
    assert "Learning: 2 learning suggestions pending" in payload["text"]
    assert "website" not in payload["text"]               # labels need explicit permission
    assert payload["learning"]["snapshot_id"] == "snap-20261021-0440"
    assert payload["learning"]["as_of"] == "2026-10-21T09:40:00Z"


def test_learning_snapshot_failure_does_not_block_the_brief(tmp_path):
    _status(tmp_path / "src")

    def boom():
        raise TimeoutError()
    spool, res = run(tmp_path, FakeIngress(), learning_source=boom, learning_principal=PRINCIPAL)
    assert res["state"] == "delivered"
    payload = _payload(spool)
    assert "Learning counts unavailable." in payload["text"]
    assert payload["learning"]["status"] == "unavailable"


def test_learning_snapshot_is_read_once_and_restart_reuses_the_rendered_brief(tmp_path):
    _status(tmp_path / "src")
    calls = []

    def source():
        calls.append(1)
        return snap()
    spool = Spool(tmp_path / "state")
    run(tmp_path, FakeIngress(mode="defer"), spool=spool, learning_source=source, learning_principal=PRINCIPAL)
    first = _payload(spool)["sha256"]
    run(tmp_path, FakeIngress(), spool=spool, now=NOW + timedelta(minutes=10),
        learning_source=lambda: snap(counts=zero_counts()), learning_principal=PRINCIPAL)
    assert len(calls) == 1 and _payload(spool)["sha256"] == first


def test_missing_principal_is_unavailable_not_unscoped(tmp_path):
    _status(tmp_path / "src")
    spool, res = run(tmp_path, FakeIngress(), learning_source=lambda: snap())
    assert _payload(spool)["learning"]["reason"] == "principal_not_configured"
