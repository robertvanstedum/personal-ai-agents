"""Rooms R3b (docs/specs/minimoi-connected-work/ROOMS_R3.md §3): logout and
credential revocation end the browser session on the server; cookies from
before R3b are refused; the rollback procedure (rotate the signing key) keeps
a revoked cookie from coming back under the older code. Bearer clients are
untouched."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

from app import create_app


@pytest.fixture
def env(tmp_path):
    app = create_app(tmp_path / "private", testing=True)
    store = app.extensions["records_store"]
    room = store.create_room("robert", str(uuid4()), dict(title="R", purpose="Synthetic", recording_acknowledged=True))["result"]["id"]
    return app, store, room


def login(app, token):
    client = app.test_client()
    assert client.post("/api/login", json={"token": token}).status_code == 200
    cookie = client.get_cookie("minimoi_room_poc")
    return client, cookie.value


def replay(app, cookie_value, path="/api/v1/rooms"):
    other = app.test_client()
    other.set_cookie("minimoi_room_poc", cookie_value)
    return other.get(path)


def test_logout_ends_the_session_and_a_copied_cookie_stops_working(env):
    app, store, room = env
    client, copied = login(app, store.owner_key)
    assert replay(app, copied).status_code == 200
    assert client.post("/api/logout", json={}).status_code == 200
    assert replay(app, copied).status_code == 401
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM web_sessions WHERE revoked IS NOT NULL").fetchone()[0] == 1


def test_a_cookie_from_before_r3b_is_refused(env):
    app, store, room = env
    client = app.test_client()
    with client.session_transaction() as sess:                      # signed, but no server-side session id
        sess["actor"] = "robert"; sess["credential_id"] = "legacy:robert"; sess.permanent = True
    assert client.get("/api/v1/rooms").status_code == 401


def test_revoking_a_credential_ends_its_web_sessions(env):
    app, store, room = env
    store.add_principal("robert", "p", dict(id="claude-code", label="Claude Code"))
    store.membership("robert", "m", room, dict(actor="claude-code"))
    issued = store.platform_access.issue("robert", dict(principal="claude-code", label="Browser",
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), grants={room: ["read"]}))
    client, cookie = login(app, issued["access_token"])
    assert replay(app, cookie, f"/api/v1/rooms/{room}").status_code == 200
    store.platform_access.revoke("robert", issued["credential_id"])
    assert replay(app, cookie, f"/api/v1/rooms/{room}").status_code == 401
    with store.connect() as db:
        assert db.execute("SELECT revoked FROM web_sessions WHERE credential_id=?", (issued["credential_id"],)).fetchone()[0]


def test_bearer_clients_never_pass_through_web_sessions(env):
    app, store, room = env
    client, cookie = login(app, store.owner_key)
    client.post("/api/logout", json={})
    bearer = app.test_client(use_cookies=False)
    assert bearer.get("/api/v1/rooms", headers={"Authorization": "Bearer " + store.owner_key}).status_code == 200


def _baseline(tmp_path):
    root = Path(__file__).resolve().parents[1]
    names = subprocess.run(["git", "ls-tree", "--full-tree", "--name-only", "a7e2e6d", "prototype-lab/projects/project-records-room-poc/"],
                           cwd=root, capture_output=True, text=True)
    if names.returncode != 0 or not names.stdout:
        pytest.skip("baseline revision a7e2e6d not available in this checkout")
    target = tmp_path / "baseline"
    (target / "integration").mkdir(parents=True)
    for name in names.stdout.split():
        leaf = name.rsplit("/", 1)[-1]
        if leaf.endswith(".py"):
            (target / leaf).write_text(subprocess.run(["git", "show", f"a7e2e6d:{name}"], cwd=root,
                                                      capture_output=True, text=True, check=True).stdout)
    for name in ("cos_records_bridge.py", "__init__.py"):
        src = subprocess.run(["git", "show", f"a7e2e6d:prototype-lab/projects/project-records-room-poc/integration/{name}"],
                             cwd=root, capture_output=True, text=True)
        (target / "integration" / name).write_text(src.stdout if src.returncode == 0 else "")
    return target


def _old_app_status(baseline, data_dir, cookie):
    code = f"""
import sys
sys.path.insert(0, {str(baseline)!r})
from app import create_app
app = create_app({str(data_dir)!r}, testing=True)
c = app.test_client()
c.set_cookie("minimoi_room_poc", {cookie!r})
print(c.get("/api/v1/rooms").status_code)
"""
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr[-2000:]
    return int(out.stdout.strip().splitlines()[-1])


def test_rollback_needs_the_key_rotation_and_the_rotation_works(env, tmp_path):
    """The hazard Codex named: older code accepts a cookie R3b revoked. The
    rollback procedure rotates session-key.txt before older code runs."""
    app, store, room = env
    baseline = _baseline(tmp_path)
    client, cookie = login(app, store.owner_key)
    client.post("/api/logout", json={})
    assert replay(app, cookie).status_code == 401
    assert _old_app_status(baseline, store.root, cookie) == 200        # the hazard, demonstrated
    key = store.root / "session-key.txt"
    tmp = key.with_suffix(".new")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(__import__("secrets").token_urlsafe(36) + "\n")
    tmp.replace(key)                                                    # what records.sh rotate-session-key does
    assert _old_app_status(baseline, store.root, cookie) == 401        # no resurrection
