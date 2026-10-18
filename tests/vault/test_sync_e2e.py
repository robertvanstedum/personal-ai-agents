"""The owner's deployment condition, scripted end to end: Mac vault-sync and EC2 vault-apply on fixtures, file:// for S3 (17 Oct 2026).

Runs ``scripts/vault/sync_e2e_fixture.py``: interrupted upload and apply, retries and an idempotent re-send, a rejected batch retried
after backoff, gap recovery to ``identical_set: true``, sleep catch-up under the daytime bound, and the status after every step.
Uses the pinned real age with a throwaway identity when installed; otherwise the transport tests' age stand-in (same flags), so the
scenario never silently skips."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("sync_e2e_fixture", ROOT / "scripts/vault/sync_e2e_fixture.py")
E = importlib.util.module_from_spec(spec)
spec.loader.exec_module(E)


def test_every_scenario_of_the_deployment_condition_passes(tmp_path):
    work = tmp_path / "e2e"
    work.mkdir()
    keygen = E.REAL_AGE.with_name("age-keygen")
    if E.REAL_AGE.is_file() and keygen.is_file():
        age_bin = str(E.REAL_AGE)
        recipient, identity = E.make_identity(work, str(keygen))
    else:
        from tests.memory_backup.test_production_transport import make_age
        age_bin, recipient, identity = make_age(tmp_path)
    report = E.run(work, age_bin, recipient, identity)
    assert report["failed"] == [], report["failed"]
    assert report["ok"] and all(report["by_scenario"].values()) and report["checks"] >= 80
