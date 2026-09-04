import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.repositories.memory import MemoryRepository


BASE_DIR = Path(__file__).resolve().parent.parent
ADMIN_HEADERS = {"X-Demo-Role": "BUSINESS_OPS_ADMIN"}


@pytest.fixture
def repository():
    return repository_for_tests()


def repository_for_tests():
    """Run the same behavior suite against memory or an opted-in PostgreSQL DB."""

    dsn = os.getenv("IOTCONNECT_TEST_POSTGRES_DSN")
    if not dsn:
        return MemoryRepository()

    from app.repositories.postgres import PostgresRepository

    repository = PostgresRepository(dsn)
    repository.reset()
    return repository


@pytest.fixture
def client(repository):
    return TestClient(create_app(repository))


def fixture_text(name: str) -> str:
    return (BASE_DIR / "fixtures" / name).read_text(encoding="utf-8")
