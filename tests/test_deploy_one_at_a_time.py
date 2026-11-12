"""Production deploys run one at a time (incident 12 Nov 2026: concurrent compose ups left services in Created)."""
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "deploy.yml"


def test_the_pipeline_queues_instead_of_running_deploys_in_parallel():
    doc = yaml.safe_load(WORKFLOW.read_text())
    conc = doc["concurrency"]
    assert conc["group"].startswith("production-deploy")
    assert conc["cancel-in-progress"] is False          # a deploy is never stopped halfway
