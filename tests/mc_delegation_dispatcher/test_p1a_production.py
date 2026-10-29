"""Host runtime P1-a, production's wiring (no database): the authority file read only when production's configuration
names it, one grant catalogue, the portal's door speaking version 2 with every caller bound to its own host, the
snapshot's read path, and Guild jobs. With nothing named, every piece is today's (the no-change proofs)."""
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.mc_delegation_dispatcher import production, server, snapshot as snap  # noqa: E402
from core.mc_delegation_dispatcher.store import Authority  # noqa: E402

PROD = ROOT / "core" / "mc_delegation_dispatcher" / "authority.prod.json"
TOKEN, HOST_TOKEN, READ_TOKEN, OTHER = "r" * 48, "h" * 48, "o" * 48, "x" * 48
FACTS = {"host_id": "mac-prod-1", "bundle_digest": "sha256:" + "a" * 64, "roles": ["worker", "witness"],
         "availability": "intermittent", "grace": 28800, "last_gap_s": 0, "announced": "online"}
CAPS = ["repo:personal-ai-agents", "scope:build", "actor:fakework", "host:mac-local"]


def _sha(t):
    return hashlib.sha256(t.encode()).hexdigest()


def _no_db():
    raise AssertionError("no database in these tests")


class FakeStore:
    """The door's calls, recorded; answers fixed."""

    def __init__(self):
        self.calls, self.reads = [], []

    def heartbeat(self, runner_id, state, caps, protocol=1, facts=None, census=None):
        self.calls.append(("heartbeat", runner_id, protocol, (facts or {}).get("host_id")))
        return {"state": "recorded"} if protocol == 2 else None

    def claim(self, runner_id, caps, protocol=1, **kw):
        self.calls.append(("claim", runner_id, protocol))
        return None

    def note_read(self, name, role):
        self.reads.append((name, role))


def _creds(tmp_path, environment="prod"):
    path = tmp_path / "door-credentials.json"
    path.write_text(json.dumps({"schema": "minimoi.door_credentials.v1", "environment": environment,
                                "hosts": {"mac-prod-1": _sha(HOST_TOKEN)},
                                "readers": {"robert": {"sha256": _sha(READ_TOKEN), "role": "owner"},
                                            "witness-ec2": {"sha256": _sha("w" * 48), "role": "observer"}}}))
    return path


def _env(**over):
    return {"MINIMOI_MC_DELEGATION": "1", "MINIMOI_MC_DELEGATION_PRINCIPAL": "robert",
            "MINIMOI_MC_DELEGATION_ENV": "prod", "MINIMOI_MC_DELEGATION_RUNNER_TOKEN_SHA256": _sha(TOKEN), **over}


def _app(monkeypatch, env, store):
    from flask import Flask
    from minimoi_portal.guild_ui import supervision
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    app = Flask("door")
    opened = supervision.attach_runner_door(app, env, store_factory=lambda: store)
    return app, opened


def _post(client, token, body):
    return client.post("/internal/mc/v1/delegation-runner", json=body, headers={"Authorization": f"Bearer {token}"})


V1 = {"op": "heartbeat", "runner_id": "mac-prod-1", "state": "online",
      "capabilities": {"actors": ["claude-code"], "scopes": ["research"], "repositories": ["personal-ai-agents"]}}
V2 = {"op": "heartbeat", "protocol": 2, "runner_id": "mac-prod-1:runner", "state": "online", "capabilities": CAPS,
      "facts": FACTS}


# ── the authority file, named or not ──
def test_unnamed_means_todays_store_and_named_means_the_version_2_store_over_one_catalogue():
    assert production.named({}) is None and production.named({production.AUTHORITY_FILE_VAR: "  "}) is None
    today = production.store(_no_db, principal="robert", environment="prod")
    assert today.authority is None and not today.sweeps
    auth = production.load(PROD, "prod")
    v2 = production.store(_no_db, principal="robert", environment="prod", authority=auth)
    assert v2.sweeps and v2.authority is auth
    assert set(v2._grants._catalogue) == {"delegate.start_build", "delegate.start_research", "delegate.card",
                                          production.SINK_OPERATION}
    from core.mc_delegation_runner import fakework
    assert production.SINK_OPERATION == fakework.OPERATION              # the portal never imports fakework.py


def test_a_named_file_that_cannot_be_used_is_refused_never_read_as_version_1(tmp_path):
    with pytest.raises(production.NotConfigured, match="authority_file_unusable:FileNotFoundError"):
        production.load(tmp_path / "missing.json", "prod")
    (tmp_path / "bad.json").write_text("{")
    with pytest.raises(production.NotConfigured, match="authority_file_unusable:authority_config_unreadable"):
        production.load(tmp_path / "bad.json", "prod")
    with pytest.raises(production.NotConfigured, match="authority_environment_mismatch"):
        production.load(PROD, "dev")
    v1 = {**json.loads(PROD.read_text()), "protocols": [1]}
    (tmp_path / "v1.json").write_text(json.dumps(v1))
    with pytest.raises(production.NotConfigured, match="authority_not_version_2"):
        production.load(tmp_path / "v1.json", "prod")
    assert production.file_digest(PROD) == "sha256:" + hashlib.sha256(PROD.read_bytes()).hexdigest()


def test_door_credentials_are_digests_for_this_environment_only(tmp_path):
    assert production.door_credentials({}, "prod") == ({}, {})
    hosts, readers = production.door_credentials({production.DOOR_CREDENTIALS_VAR: str(_creds(tmp_path))}, "prod")
    assert hosts == {"mac-prod-1": _sha(HOST_TOKEN)} and readers["robert"]["role"] == "owner"
    with pytest.raises(production.NotConfigured, match="door_credentials_invalid"):
        production.door_credentials({production.DOOR_CREDENTIALS_VAR: str(_creds(tmp_path / "..", "dev"))}, "prod")


def test_the_grace_cap_cuts_a_hosts_own_grace_to_its_availabilitys(monkeypatch):
    auth = Authority.load(PROD)
    store = production.store(_no_db, principal="robert", environment="prod", authority=auth)
    since = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)

    class Cur:
        def __init__(self, owner):
            self.owner = owner

        def execute(self, *a):
            pass

        def fetchone(self):
            return self.owner

        def fetchall(self):
            return []

    for availability, claimed, want in (("intermittent", 7 * 86400, 28800), ("always_on", 28800, 1800),
                                        ("intermittent", 900, 900)):
        cur = Cur({"unreachable_grace_s": claimed, "expected_availability": availability})
        deadline, down, grace_s, _ = store._grace(cur, {"runner_id": "mac-prod-1:runner"}, since)
        assert (grace_s, deadline) == (want, since + timedelta(seconds=want)), availability


# ── the portal's runner door ──
def test_unnamed_the_door_is_todays_word_for_word(monkeypatch):
    store = FakeStore()
    app, opened = _app(monkeypatch, _env(), store)
    assert opened and "runtime_state" not in app.view_functions
    c = app.test_client()
    r = _post(c, TOKEN, V1)
    assert (r.status_code, r.get_json()) == (200, {"value": None}) and store.calls == [("heartbeat", "mac-prod-1", 1, None)]
    r = _post(c, TOKEN, V2)                       # today: the store refuses version 2 (here, the fake has no authority)
    assert r.status_code in (200, 400)
    assert _post(c, OTHER, V1).status_code == 401


def test_named_the_door_binds_every_version_2_caller_to_its_own_host(monkeypatch, tmp_path):
    store = FakeStore()
    env = _env(**{production.AUTHORITY_FILE_VAR: str(PROD), production.DOOR_CREDENTIALS_VAR: str(_creds(tmp_path))})
    app, opened = _app(monkeypatch, env, store)
    assert opened
    c = app.test_client()
    assert _post(c, TOKEN, V1).get_json() == {"value": None}                       # version 1: today's token, answer
    r = _post(c, HOST_TOKEN, V2)
    assert (r.status_code, r.get_json()) == (200, {"value": {"state": "recorded"}})
    assert store.calls[-1] == ("heartbeat", "mac-prod-1:runner", 2, "mac-prod-1")
    n = len(store.calls)
    r = _post(c, HOST_TOKEN, {**V2, "runner_id": "mac-evil:runner"})
    assert (r.status_code, r.get_json()) == (403, {"error": "runner_not_this_credential"})
    r = _post(c, HOST_TOKEN, {**V2, "facts": {**FACTS, "host_id": "mac-b"}})
    assert (r.status_code, r.get_json()) == (403, {"error": "host_not_this_credential"})      # Codex part 2 P2-1
    assert _post(c, TOKEN, V2).status_code == 401                          # the version 1 token speaks no version 2
    assert _post(c, HOST_TOKEN, V1).status_code == 401                     # nor a host token version 1
    assert len(store.calls) == n                                           # none of those reached the store


def test_named_an_authenticated_host_is_never_rate_limited_and_strangers_are(monkeypatch, tmp_path):
    store = FakeStore()
    env = _env(**{production.AUTHORITY_FILE_VAR: str(PROD), production.DOOR_CREDENTIALS_VAR: str(_creds(tmp_path))})
    from flask import Flask
    from minimoi_portal.guild_ui import supervision
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    app = Flask("door")
    assert supervision.attach_runner_door_v2(app, env, _sha(TOKEN), "robert", "prod", store_factory=lambda: store,
                                             per_minute=3)
    c = app.test_client()
    statuses = [_post(c, OTHER, V2).status_code for _ in range(5)]
    assert statuses[:3] == [401, 401, 401] and statuses[3:] == [429, 429]
    assert all(_post(c, HOST_TOKEN, V2).status_code == 200 for _ in range(10))       # S1-H review SF4


def test_named_but_unusable_the_door_stays_closed(monkeypatch, tmp_path):
    (tmp_path / "bad.json").write_text("{")
    for env in (_env(**{production.AUTHORITY_FILE_VAR: str(tmp_path / "bad.json")}),
                _env(**{production.AUTHORITY_FILE_VAR: str(PROD),
                        production.DOOR_CREDENTIALS_VAR: str(tmp_path / "missing.json")})):
        app, opened = _app(monkeypatch, env, FakeStore())
        assert opened is False and "delegation_runner" not in app.view_functions


def test_the_read_path_opens_only_with_readers_and_the_published_snapshot(monkeypatch, tmp_path):
    store = FakeStore()
    state_dir = tmp_path / "authority"
    doc = {"schema": snap.SCHEMA, "environment": "prod", "as_of": datetime.now(timezone.utc).isoformat(),
           "source": "authority", "view_age_s": 0, "items": [], "hosts": [], "notices": [], "decisions_waiting": []}
    snap.write_state_file(state_dir, doc)
    base = {production.AUTHORITY_FILE_VAR: str(PROD), production.DOOR_CREDENTIALS_VAR: str(_creds(tmp_path))}
    app, _ = _app(monkeypatch, _env(**base), store)
    assert "runtime_state" not in app.view_functions                       # no state file named: not opened
    env = _env(**base, **{production.STATE_FILE_VAR: str(state_dir / "state.json")})
    app, _ = _app(monkeypatch, env, store)
    c = app.test_client()
    r = c.get(server.STATE_PATH, headers={"Authorization": f"Bearer {READ_TOKEN}"})
    body = r.get_json()
    assert r.status_code == 200 and body["schema"] == snap.SCHEMA and body["view_age_s"] is not None
    assert r.headers["Cache-Control"] == "no-store" and store.reads == [("robert", "owner")]
    assert c.get(server.STATE_PATH, headers={"Authorization": f"Bearer {HOST_TOKEN}"}).status_code == 401
    assert c.get(server.STATE_PATH).status_code == 401


# ── Guild jobs ──
def test_guild_jobs_build_todays_store_unless_the_file_is_named(monkeypatch, tmp_path, caplog):
    from minimoi_portal.guild_ui import jobs_wiring
    sentinel = object()
    env = {"MINIMOI_MC_DELEGATION": "1", "MINIMOI_MC_DELEGATION_PRINCIPAL": "robert",
           "MINIMOI_MC_DELEGATION_ENV": "prod", "DATABASE_URL": "postgresql://unused"}
    routed = jobs_wiring._with_delegation(sentinel, env)
    assert routed.delegation.store.authority is None                       # today's version 1 store
    routed = jobs_wiring._with_delegation(sentinel, {**env, production.AUTHORITY_FILE_VAR: str(PROD)})
    assert routed.delegation.store.sweeps                                  # production's version 2 store
    (tmp_path / "bad.json").write_text("{")
    assert jobs_wiring._with_delegation(sentinel, {**env, production.AUTHORITY_FILE_VAR: str(tmp_path / "bad.json")}) \
        is sentinel                                                        # named and refused: delegation stays off
    assert jobs_wiring._with_delegation(sentinel, {}) is sentinel          # off: today's relay untouched
