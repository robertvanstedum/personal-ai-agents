"""The automated gate M subset, end to end through the controller (S1-H), on the throwaway PostgreSQL server that
build-s1/tools/run_pg_tests.py starts (its ``mc12_test_*`` DSN names the server; the controller creates
``minimoi_runtime_acceptance`` there by the same migrations as dev).

Opt-in twice, because it runs for about forty minutes: ``MC_BINDING_TEST_DSN`` (run_pg_tests.py sets it) and
``HARNESS_EVIDENCE`` (the evidence folder for this run, which must not exist yet). ``HARNESS_ONLY`` limits it to some
scenarios. The throwaway server's container (run_pg_tests.py's ``mc12-test-pg-harness``) is T8's and X5's authority
to stop; without it those two are recorded ``not_run``. A failure is kept in the evidence pack, never rerun."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

DSN = os.environ.get("MC_BINDING_TEST_DSN")
EVIDENCE = os.environ.get("HARNESS_EVIDENCE")
CONTAINER = "mc12-test-pg-harness"


def _container():
    """run_pg_tests.py's container, only when it is the one serving the DSN's port."""
    from psycopg2.extensions import parse_dsn
    port = parse_dsn(DSN).get("port", "5432")
    out = subprocess.run(["docker", "port", CONTAINER, "5432/tcp"], capture_output=True, text=True)
    return CONTAINER if out.returncode == 0 and out.stdout.strip().endswith(f":{port}") else None


@pytest.mark.skipif(not (DSN and EVIDENCE), reason="opt-in: MC_BINDING_TEST_DSN and HARNESS_EVIDENCE")
def test_the_automated_gate_m_subset():
    from harness_acceptance import controller
    args = ["run", "--plan", controller.PLAN, "--evidence", EVIDENCE]
    container = _container()
    if container:
        args += ["--authority-container", container]
    if os.environ.get("HARNESS_ONLY"):
        args += ["--only", os.environ["HARNESS_ONLY"]]
    code = controller.main(args)
    receipt = json.loads((Path(EVIDENCE) / "receipt.json").read_text())
    print(json.dumps({k: receipt[k] for k in ("checks", "failed", "not_run", "scenarios")}, indent=1, sort_keys=True))
    assert code == 0 and not receipt["failed"], receipt["failed"]
