"""Production deploys run one at a time (incident 12 Nov 2026: concurrent compose ups left services in Created)."""
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "deploy.yml"


def test_the_pipeline_queues_instead_of_running_deploys_in_parallel():
    doc = yaml.safe_load(WORKFLOW.read_text())
    conc = doc["concurrency"]
    assert conc["group"].startswith("production-deploy")
    assert conc["cancel-in-progress"] is False          # a deploy is never stopped halfway


def test_a_push_compares_against_the_last_successful_deploy():
    """A queued run replaced by a newer one must not lose its services: the newer run diffs from the last good deploy."""
    doc = yaml.safe_load(WORKFLOW.read_text())
    classify = doc["jobs"]["classify"]
    assert classify["permissions"] == {"contents": "read", "actions": "read"}
    step = next(s for s in classify["steps"] if s.get("name") == "Classify release contents")
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert "status=success" in step["run"] and 'release_base.sh "$PUSH_BEFORE"' in step["run"]
    assert "merge-base --is-ancestor" in step["run"]
