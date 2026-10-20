"""Shared opt-in setup for actual PostgreSQL tests, never an application DB."""

import os
from pathlib import Path

DSN = os.environ.get('MC_BINDING_TEST_DSN')


class PostgresFixture:
    migrations = ()

    @classmethod
    def setUpClass(cls):
        import psycopg2
        from psycopg2.extensions import parse_dsn
        params = parse_dsn(DSN)
        host = params.get('host', '')
        if (not params.get('dbname', '').startswith('mc12_test_')
                or not (host in {'', 'localhost', '127.0.0.1', '::1'} or host.startswith('/'))):
            raise RuntimeError('an isolated local mc12_test_* database is required')
        cls.connect = staticmethod(lambda: psycopg2.connect(
            DSN, connect_timeout=3,
            options='-c statement_timeout=4000 -c lock_timeout=3000'))
        cls.guard = cls.connect()
        try:
            with cls.guard.cursor() as cur:
                cur.execute('SELECT current_database(), pg_try_advisory_lock(731241902)')
                name, locked = cur.fetchone()
                if not name.startswith('mc12_test_') or not locked:
                    raise RuntimeError('test database unavailable or already in use')
                cur.execute('CREATE SCHEMA IF NOT EXISTS guild')
            cls.guard.commit()
            repo = Path(__file__).resolve().parents[2]
            for migration in cls.migrations:
                with cls.guard.cursor() as cur:
                    cur.execute((repo / migration).read_text())
                cls.guard.commit()
        except Exception:
            cls.guard.close()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.guard.close()
