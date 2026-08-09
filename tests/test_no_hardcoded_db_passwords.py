"""
tests/test_no_hardcoded_db_passwords.py — issue #42.

A hardcoded "simple123" fallback password was scattered across ~17 files
(Postgres DSN defaults, direct psycopg2.connect() calls, and Neo4j's
NEO4J_AUTH) even though the real passwords were rotated off it in July
2026 — each fallback was already-dead code that just looked like a live
credential. Rather than relying on catching every future reintroduction by
code review, this test greps the tracked source tree directly so a new
hardcoded default anywhere fails CI immediately.
"""
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Files allowed to mention the string — the test that guards against a
# regression in Portuguese's db connection (asserts the string is ABSENT
# from db_conn's source, so the string appears in the assertion itself),
# this file's own docstring, and docs describing the historical rotation.
ALLOWED = {
    "tests/test_portuguese_db_and_state.py",
    "tests/test_no_hardcoded_db_passwords.py",
    "docs/DB_SCHEMA.md",
}


def test_simple123_not_hardcoded_anywhere_else():
    result = subprocess.run(
        ["git", "grep", "-l", "simple123", "--", "*.py", "*.yml", "*.yaml", "*.sh"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    # git grep exits 1 when there are no matches at all -- that's a pass.
    if result.returncode not in (0, 1):
        raise RuntimeError(f"git grep failed: {result.stderr}")
    found = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    unexpected = found - ALLOWED
    assert not unexpected, (
        f"Hardcoded 'simple123' fallback found outside the allow-list: {unexpected}. "
        "If this is a real new use, decide whether it belongs on the allow-list here "
        "or should read the real credential from an env var instead (issue #42)."
    )
