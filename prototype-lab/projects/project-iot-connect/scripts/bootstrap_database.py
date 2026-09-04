"""Prepare the PostgreSQL demo database before IoT Connect starts.

Idempotent by design:
- waits for the database to accept connections;
- applies ``postgres/01_schema.sql`` (all statements are IF NOT EXISTS /
  ON CONFLICT, so re-applying is safe and refreshes the catalog rows);
- seeds the deterministic demo data only when ``iot.accounts`` is empty, so a
  restart never wipes demonstrated evidence. ``make reset`` is the explicit
  path back to the seed state.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import psycopg

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:  # allow `python scripts/bootstrap_database.py`
    sys.path.insert(0, str(BASE_DIR))

from app.repositories import UNSUPPORTED_STORE_MESSAGE  # noqa: E402
from app.repositories.postgres import PostgresRepository  # noqa: E402

SCHEMA_PATH = BASE_DIR / "postgres" / "01_schema.sql"


def wait_for_database(dsn: str, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(dsn, connect_timeout=3) as connection:
                connection.execute("SELECT 1")
            return
        except psycopg.Error as exc:  # pragma: no cover - timing dependent
            last_error = exc
            time.sleep(1)
    raise SystemExit(f"PostgreSQL not reachable within {timeout_seconds:.0f}s: {last_error}")


def apply_schema(dsn: str) -> None:
    statements = SCHEMA_PATH.read_text(encoding="utf-8")
    with psycopg.connect(dsn) as connection:
        connection.execute(statements)


def seed_if_empty(dsn: str) -> str:
    with psycopg.connect(dsn) as connection:
        row = connection.execute("SELECT count(*) FROM iot.accounts").fetchone()
    if row and int(row[0]) > 0:
        return "existing data retained"
    result = PostgresRepository(dsn).reset()
    return f"seeded ({result['accounts_seeded']} accounts, {result['sim_resources_seeded']} SIMs, {result['mdn_resources_seeded']} MDNs)"


def main() -> int:
    backend = os.getenv("IOTCONNECT_STORE", "memory").lower()
    if backend == "memory":
        print("bootstrap: store is 'memory', no database preparation needed")
        return 0
    if backend not in {"postgres", "postgresql"}:
        print("bootstrap: " + UNSUPPORTED_STORE_MESSAGE.format(value=backend), file=sys.stderr)
        return 1
    dsn = os.getenv("POSTGRES_DSN")
    if not dsn:
        print("bootstrap: POSTGRES_DSN is not set", file=sys.stderr)
        return 1
    timeout = float(os.getenv("IOTCONNECT_DB_WAIT_SECONDS", "90"))
    wait_for_database(dsn, timeout)
    apply_schema(dsn)
    print(f"bootstrap: schema applied; {seed_if_empty(dsn)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
