"""Records (Rooms) for the Guild 1.1 slice 4 tests: the real Records app from
prototype-lab, on a temporary data folder, reached through the portal's
bridge by a transport that calls Records' own test client (no network).
Every credential here is a test value Records generates in the temporary
folder; nothing touches a real Records database.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

RECORDS_DIR = Path(__file__).resolve().parents[3] / "prototype-lab" / "projects" / "project-records-room-poc"
BACKEND = "http://minimoi-records:18880"
DEV = "https://dev.minimoi.ai"


def records_module():
    if str(RECORDS_DIR) not in sys.path:
        sys.path.append(str(RECORDS_DIR))         # after everything else: its bare module names never shadow ours
    name = "records_poc_app_for_tests"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RECORDS_DIR / "app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def records_app(data_dir: Path, *, hosts=("minimoi-records:18880",), port=18880):
    data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    data_dir.chmod(0o700)
    return records_module().create_app(str(data_dir), port=port, allowed_hosts=set(hosts))


class RecordsTransport:
    """What the bridge's requests.Session would do, against Records' test
    client: the exact method, URL and headers, recorded for the tests."""

    def __init__(self, app):
        self.app = app
        self.client = app.test_client(use_cookies=False)
        self.calls = []

    def request(self, method, url, headers=None, data=None, timeout=None, allow_redirects=None):
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}), "allow_redirects": allow_redirects})
        parts = urlsplit(url)
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        response = self.client.open(path, method=method, headers=dict(headers or {}), data=data,
                                    base_url=f"{parts.scheme}://{parts.netloc}")
        return SimpleNamespace(content=response.data, status_code=response.status_code, headers=response.headers)


def owner_key(app) -> str:
    return (Path(app.extensions["records_store"].root) / "owner-key.txt").read_text().strip()


def dev_owner(portal):
    """A portal client signed in as the owner on the dev host."""
    from floor_helpers import OWNER
    client = portal.app.test_client()
    with client.session_transaction(base_url=DEV) as sess:
        sess["user"] = dict(OWNER)
    return client


def records_login(client, token: str):
    return client.post("/app/records/api/login", base_url=DEV, headers={"Origin": DEV},
                       data=json.dumps({"token": token}), content_type="application/json")


def rpost(client, path, body, key, origin=DEV):
    headers = {"Idempotency-Key": key}
    if origin:
        headers["Origin"] = origin
    return client.post(f"/app/records/api{path}", base_url=DEV, headers=headers, data=json.dumps(body),
                       content_type="application/json")


def rget(client, path):
    return client.get(f"/app/records/api{path}", base_url=DEV)
