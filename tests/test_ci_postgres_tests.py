"""CI runs the opt-in PostgreSQL tests (MC_BINDING_TEST_DSN) on a throwaway database, so they cannot skip unseen.

Until 29 Oct 2026 no workflow set the DSN: about 400 database tests skipped in CI, and 24 of them had been failing
on main for days without anyone seeing it."""
from pathlib import Path

import yaml
from psycopg2.extensions import parse_dsn

ROOT = Path(__file__).resolve().parents[1]


def _test_job(workflow):
    return yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text())["jobs"]["test"]


def test_both_test_jobs_give_the_database_tests_a_throwaway_postgres():
    for workflow in ("deploy.yml", "nightly-tests.yml"):
        job = _test_job(workflow)
        postgres = job["services"]["postgres"]
        assert postgres["env"]["POSTGRES_HOST_AUTH_METHOD"] == "trust", workflow
        suite = next(step for step in job["steps"] if "pytest tests/" in step.get("run", ""))
        dsn = parse_dsn(suite["env"]["MC_BINDING_TEST_DSN"])
        # The fixture (tests/minimoi_actions/postgres_fixture_support.py) refuses all but a local mc12_test_* database.
        assert dsn["dbname"].startswith("mc12_test_") and dsn["dbname"] == postgres["env"]["POSTGRES_DB"], workflow
        assert dsn["host"] == "127.0.0.1" and str(postgres["ports"][0]) == f"{dsn['port']}:5432", workflow
