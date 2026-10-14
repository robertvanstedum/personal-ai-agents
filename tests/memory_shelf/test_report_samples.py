"""The committed sample payloads (docs/memory_capture_report_samples/) are exactly what the producer writes."""
import json
from pathlib import Path

import pytest

from core.memory_shelf import report

from . import report_samples

DOCS = Path(__file__).resolve().parents[2] / "docs" / "memory_capture_report_samples"
SAMPLES = report_samples.make_samples()


@pytest.mark.parametrize("name", report_samples.SCENARIOS)
def test_the_committed_samples_match_the_producer(name):
    doc = SAMPLES[name]
    assert (DOCS / f"{name}.report.json").read_text() == report_samples.render(doc), "run scripts/memory/make_report_samples.py"
    assert (DOCS / f"{name}.matrix.json").read_text() == report_samples.render(doc["operate"])
    report.assert_safe(doc)


def test_the_samples_cover_every_state_a_row_can_take():
    seen = {r["status"] for doc in SAMPLES.values() for r in doc["operate"]["rows"]}
    assert seen == {"current", "watch", "unknown"}                                            # off is covered by the unit tests
    reasons = {r["reason"] for doc in SAMPLES.values() for r in doc["operate"]["rows"]}
    assert {"ok", "overdue", "capture_failed", "records_on_older_parser", "fidelity_failed", "no_state", "quality_unknown"} <= reasons


def test_every_sample_has_the_frozen_top_level_shape():
    keys = {"schema", "run_id", "generated_at", "source_snapshot", "versions", "cadence", "job", "sources", "totals", "history", "operate"}
    for doc in SAMPLES.values():
        assert set(doc) == keys and doc["schema"] == report.SCHEMA and doc["operate"]["schema"] == report.MATRIX_SCHEMA
        for src in doc["sources"].values():
            assert set(src) == {"label", "mode", "capture", "selection", "quality", "evidence", "status"}
