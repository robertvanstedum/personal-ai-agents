"""Shared helpers and fixtures for the /guild-next tests (B1 deliverable (b)).

There is deliberately no conftest.py in this folder: another test in the tree
does ``from conftest import …``, and a second module named ``conftest`` would
shadow it. Test modules import the fixtures from here instead.

``load_portal`` loads a fresh copy of minimoi_portal/app.py under a unique
module name, with the staging switches and a temporary queue folder set, so
the tests exercise the portal's own registration hook, owner guard and
catch-all route as the staging container would. Nothing here makes a network
call: the secret lookup is replaced before the copy is loaded, and every test
queue lives in a temporary folder outside the code tree.
"""
from __future__ import annotations

import importlib.util
import itertools
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from domains.guild import queue_store as qs

REPO = Path(__file__).resolve().parents[3]
OWNER = {"username": "robert", "tier": "owner", "display_name": "Robert", "auth_id": 1}
GUEST = {"username": "guest_0badf00d", "tier": "guest", "display_name": "Guest"}
_COUNTER = itertools.count()

# Deliberately unlike anything in the prototype's scenario (no #158, no s-023).
QUEUE_ITEMS = [
    {"id": 7, "spec_title": "Queue lock hardening", "status": "spec_ready", "summary": "lock first",
     "last_transition_at": "2026-09-20T10:00:00+00:00"},
    {"id": 12, "spec_title": "Floor API", "status": "in_build", "summary": "json api",
     "spec_file": "spec_floor_api.md", "last_transition_at": "2026-09-21T10:00:00+00:00"},
    {"id": 31, "spec_title": "Blocked thing", "status": "blocked", "blocked_reason": "waiting on Robert",
     "last_transition_at": "2026-09-22T10:00:00+00:00"},
    {"id": 40, "spec_title": "Done thing", "status": "done", "last_transition_at": "2026-09-01T10:00:00+00:00"},
]


def write_queue(path: Path, items=None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(qs.serialize(QUEUE_ITEMS if items is None else items))
    return path


@dataclass
class Portal:
    module: object
    queue_path: Path | None
    extra: dict = field(default_factory=dict)

    @property
    def app(self):
        return self.module.app

    def client(self, user=None):
        client = self.app.test_client()
        if user is not None:
            with client.session_transaction() as sess:
                sess["user"] = dict(user)
        return client

    def owner(self):
        return self.client(OWNER)

    def guest(self):
        return self.client(GUEST)

    @staticmethod
    def csrf(client, prefix="/guild-next"):
        response = client.get(f"{prefix}/api/v1/session")
        assert response.status_code == 200, response.data
        return response.get_json()["csrf_token"]

    def journal(self):
        path = self.queue_path.parent / qs.JOURNAL_NAME
        if not path.exists():
            return []
        return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def write_headers(token, *, mode="on_record", origin=None, fetch_site=None):
    headers = {"X-CSRF-Token": token, "X-Record-Mode": mode}
    if origin:
        headers["Origin"] = origin
    if fetch_site:
        headers["Sec-Fetch-Site"] = fetch_site
    return headers


@pytest.fixture
def load_portal(monkeypatch, tmp_path):
    """Return a loader: load_portal(next_flag=..., proto_flag=..., queue=...)."""
    import core.get_secret as secrets_module
    import minimoi_portal.config as portal_config

    def _no_secret(*_args, **_kwargs):
        raise RuntimeError("tests never read secrets")

    monkeypatch.setattr(secrets_module, "get_secret", _no_secret)
    monkeypatch.setattr(qs, "_running_in_container", lambda: False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("GUILD_RECORDS_DB", raising=False)
    monkeypatch.delenv("SENTRY_DSN", raising=False)

    def _load(*, next_flag: str | None = "1", proto_flag: str | None = None,
              queue: str | Path | None | bool = True, base_url="https://dev.minimoi.ai",
              ops_url: str | None = None, items=None) -> Portal:
        for name, value in (("MINIMOI_GUILD_NEXT", next_flag), ("MINIMOI_GUILD_PROTO", proto_flag)):
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)
        if queue is True:
            queue_path = write_queue(tmp_path / f"state{next(_COUNTER)}" / "guild" / "build_queue.json", items)
        elif queue in (None, False):
            queue_path = None
        else:
            queue_path = Path(queue)
        monkeypatch.setattr(portal_config, "GUILD_QUEUE_PATH", str(queue_path) if queue_path else None)
        monkeypatch.setattr(portal_config, "BASE_URL", base_url)
        monkeypatch.setattr(portal_config, "GUILD_OPERATIONS_STATUS_URL", ops_url)
        name = f"portal_app_staging_{next(_COUNTER)}"
        spec = importlib.util.spec_from_file_location(name, REPO / "minimoi_portal" / "app.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.app.config["TESTING"] = True
        return Portal(module, queue_path)

    return _load


@pytest.fixture
def staging(load_portal):
    """The staging portal with /guild-next on and a healthy temporary queue."""
    return load_portal()
