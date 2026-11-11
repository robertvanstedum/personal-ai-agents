"""A newer approved charter starts new work; a task chartered under an older approved version still finds its charter
(Dev, 11 Nov 2026: raising helper limits to v2 dropped returns already in flight under v1)."""
import json
import shutil
from pathlib import Path

from core.canonical_json import digest
from services.partnership_tasks.charters import Charters

SRC = Path(__file__).resolve().parents[2] / "config/partnership/charters"


def _with_v2(tmp_path):
    root = tmp_path / "charters"
    shutil.copytree(SRC, root, ignore=shutil.ignore_patterns("approvals.json"))
    approvals = {}
    for seat in ("apprentice", "ea"):
        v1 = json.loads((root / seat / "v1.json").read_text())
        v2 = dict(v1, version=2, daily_budget_usd=10.0)
        (root / seat / "v2.json").write_text(json.dumps(v2))
        for doc in (v1, v2):
            approvals[f"{seat}@{doc['version']}"] = {"digest": digest(doc), "approved_by": "robert", "ref": "test", "environment": "dev"}
    (tmp_path / "approvals.json").write_text(json.dumps(approvals))
    return Charters.load(root, tmp_path / "approvals.json"), approvals


def test_new_work_uses_the_newest_and_old_tasks_keep_theirs(tmp_path):
    charters, approvals = _with_v2(tmp_path)
    assert charters.get("apprentice").version == 2 and charters.get("apprentice").daily_budget_usd == 10.0
    assert charters.get("apprentice", 1).version == 1
    old_ref = {"seat": "apprentice", "version": 1, "digest": approvals["apprentice@1"]["digest"], "type_id": "repo-lookup"}
    found = charters.chartered(old_ref)
    assert found is not None and found[0].version == 1
    assert charters.covers("charter:apprentice:1", "mc", "personal") and charters.covers("charter:apprentice:2", "mc", "personal")
    assert charters.get("apprentice", 3) is None
