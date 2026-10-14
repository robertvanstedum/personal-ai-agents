"""The Ask service binds identity to a token (Codex lane, 5 Oct 2026): spoofed identity, owner scopes, caller isolation,
revocation, rotation, fail-closed registry, limits. Synthetic records and a fake embedder only; no network, no real token."""
from __future__ import annotations

import json
import os
import stat

import pytest

from core.memory_ask import agents_cli, identity, querylog, service
from core.memory_index import indexer
from tests.memory_index.helpers import FakeEmbedder, add_record, make_shelf


@pytest.fixture
def world(tmp_path):
    shelf = make_shelf(tmp_path)
    add_record(shelf, "grok", "g1", [("human", "How do I make my sourdough rise better?"),
                                     ("assistant", "Feed the starter twice a day and proof the dough somewhere warm.")])
    emb = FakeEmbedder()
    assert indexer.update(shelf.root, emb, min_free_bytes=0)["code"] == "ok"
    reg = identity.registry_path(shelf.root)
    tokens = {"mc": identity.add_agent(reg, "mc"),
              "cos": identity.add_agent(reg, "cos", tiers=("curated", "all"), max_k=3)}
    app = service.create_app(registry_path=reg, shelf_root=shelf.root, embedder_factory=lambda: FakeEmbedder())
    return shelf, reg, tokens, app.test_client()


def post(client, token, body=None, **kw):
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    headers.update(kw.pop("headers", {}))
    return client.post("/v1/ask", json=body if body is not None else {"query": "sourdough starter"}, headers=headers, **kw)


def log_rows(shelf):
    path = querylog.path_for(indexer.default_index_path(shelf.root))
    return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


# ── the registry itself ──────────────────────────────────────────────────────

def test_the_registry_holds_a_hash_never_the_token_and_is_private(world):
    shelf, reg, tokens, _ = world
    text = reg.read_text()
    assert all(t not in text for t in tokens.values())
    assert identity.token_hash(tokens["mc"]) in text
    assert stat.S_IMODE(reg.stat().st_mode) == 0o600 and stat.S_IMODE(reg.parent.stat().st_mode) == 0o700
    assert len(tokens["mc"]) >= 40                                            # 256 random bits, urlsafe


@pytest.mark.parametrize("name", ["robert", "owner", "canary", "Admin", "mc!", "1mc", "m", "x" * 40, "", "mc mc"])
def test_reserved_and_malformed_agent_names_are_refused(tmp_path, name):
    with pytest.raises(identity.RegistryError):
        identity.add_agent(tmp_path / "memory-ask" / "agents.json", name)


@pytest.mark.parametrize("kwargs", [{"tiers": ("all",)}, {"tiers": ("curated", "raw")}, {"scopes": ("everyone",)}, {"scopes": ()},
                                    {"max_k": 0}, {"max_k": True}, {"rate_per_min": 5000}, {"entry_point": "A B"}])
def test_a_malformed_grant_is_refused_before_anything_is_written(tmp_path, kwargs):
    path = tmp_path / "memory-ask" / "agents.json"
    with pytest.raises(identity.RegistryError):
        identity.add_agent(path, "mc", **kwargs)
    assert not path.exists()


def test_an_existing_name_cannot_be_added_again(world):
    _, reg, _, _ = world
    with pytest.raises(identity.RegistryError) as exc:
        identity.add_agent(reg, "mc")
    assert exc.value.code == "agent_exists"


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o644, 0o666, 0o660])
def test_a_registry_readable_by_others_fails_closed(world, mode):
    shelf, reg, tokens, client = world
    os.chmod(reg, mode)
    r = post(client, tokens["mc"])
    assert r.status_code == 503 and r.get_json() == {"error": "registry_unavailable"}
    os.chmod(reg, 0o600)
    assert post(client, tokens["mc"]).status_code == 200                      # and recovers when it is private again


@pytest.mark.parametrize("content", ["{not json", "[]", '{"schema": "other", "agents": {}}', '{"schema": "%s"}' % identity.SCHEMA])
def test_a_corrupt_registry_denies_everyone_then_recovers(world, content):
    shelf, reg, tokens, client = world
    good = reg.read_text()
    reg.write_text(content)
    assert post(client, tokens["mc"]).status_code == 503 and post(client, "x" * 40).status_code == 503
    reg.write_text(good)
    os.utime(reg, ns=(reg.stat().st_atime_ns, reg.stat().st_mtime_ns + 5_000_000_000))
    assert post(client, tokens["mc"]).status_code == 200


def test_a_missing_registry_denies_everyone(tmp_path):
    shelf = make_shelf(tmp_path)
    app = service.create_app(registry_path=tmp_path / "nope" / "agents.json", shelf_root=shelf.root, embedder_factory=FakeEmbedder)
    assert post(app.test_client(), "t" * 43).status_code == 503


def test_one_token_can_never_name_two_agents(world):
    _, reg, tokens, _ = world
    doc = json.loads(reg.read_text())
    doc["agents"]["cos"]["token_sha256"] = doc["agents"]["mc"]["token_sha256"]
    reg.write_text(json.dumps(doc))
    with pytest.raises(identity.RegistryError):
        identity.load(reg)


# ── authentication: no oracle ────────────────────────────────────────────────

@pytest.mark.parametrize("header", [None, "", "Bearer", "Bearer ", "Basic abc", "bearer", "Token xyz", "Bearer " + "a" * 400,
                                    "Bearer short"])
def test_a_missing_or_malformed_credential_is_unauthorized(world, header):
    shelf, _, _, client = world
    r = client.post("/v1/ask", json={"query": "sourdough"}, headers={"Authorization": header} if header is not None else {})
    assert r.status_code == 401 and r.get_json() == {"error": "unauthorized"}
    assert log_rows(shelf) == []


def test_unknown_wrong_and_revoked_tokens_are_indistinguishable(world):
    shelf, reg, tokens, client = world
    identity.revoke_agent(reg, "mc")
    bodies = {json.dumps(post(client, t).get_json(), sort_keys=True) for t in ("z" * 43, tokens["mc"], tokens["cos"][:-1] + "x")}
    codes = {post(client, t).status_code for t in ("z" * 43, tokens["mc"])}
    assert bodies == {'{"error": "unauthorized"}'} and codes == {401}


# ── spoofing: the token decides, nothing else ────────────────────────────────

@pytest.mark.parametrize("extra", [{"agent": "cos"}, {"entry_point": "chat"}, {"scopes": ["mandate:x"]}, {"canary": True},
                                   {"tier": "raw"}, {"caller": {"agent": "robert"}}, {"identity": "robert"}])
def test_a_body_that_names_an_identity_scope_tier_or_canary_is_refused(world, extra):
    shelf, _, tokens, client = world
    r = post(client, tokens["mc"], {"query": "sourdough", **extra})
    assert r.status_code == 400 and r.get_json() == {"error": "bad_arguments"}
    assert log_rows(shelf) == []


@pytest.mark.parametrize("headers", [{"X-Agent": "cos"}, {"X-Minimoi-User-Tier": "owner"}, {"X-Minimoi-Username": "robert"},
                                     {"X-Forwarded-User": "robert"}, {"X-Entry-Point": "chat"}])
def test_identity_headers_and_the_query_string_are_ignored(world, headers):
    shelf, _, tokens, client = world
    r = client.post("/v1/ask?agent=cos&entry_point=chat&tier=raw", json={"query": "sourdough"},
                    headers={"Authorization": f"Bearer {tokens['mc']}", **headers})
    assert r.status_code == 200 and r.get_json()["caller"] == {"agent": "mc", "entry_point": "tool"}
    assert [row["caller"] for row in log_rows(shelf)] == [{"agent": "mc", "entry_point": "tool"}]


def test_each_agent_is_logged_under_its_own_name_and_cannot_borrow_anothers(world):
    shelf, _, tokens, client = world
    a, b = post(client, tokens["mc"]), post(client, tokens["cos"])
    assert a.get_json()["caller"]["agent"] == "mc" and b.get_json()["caller"]["agent"] == "cos"
    assert [row["caller"]["agent"] for row in log_rows(shelf)] == ["mc", "cos"]
    assert a.get_json()["keyword"] == [] or a.get_json()["tier"] == "curated"       # mc did not get the raw tier


# ── owner scopes and tiers come from the grant ───────────────────────────────

def test_the_raw_tier_needs_the_grant_and_is_refused_not_silently_downgraded(world):
    shelf, _, tokens, client = world
    r = post(client, tokens["mc"], {"query": "sourdough", "include_raw": True})
    assert r.status_code == 403 and r.get_json() == {"error": "tier_not_permitted"}
    assert log_rows(shelf) == []
    ok = post(client, tokens["cos"], {"query": "sourdough", "include_raw": True})
    assert ok.status_code == 200 and ok.get_json()["tier"] == "all" and ok.get_json()["keyword"]


def test_without_the_raw_grant_the_default_search_is_curated_only(world):
    shelf, _, tokens, client = world
    r = post(client, tokens["mc"], {"query": "sourdough"})
    assert r.get_json()["tier"] == "curated" and r.get_json()["keyword"] == []      # nothing is designated curated yet


def test_the_scopes_come_from_the_grant_and_filter_what_is_seen(tmp_path):
    shelf = make_shelf(tmp_path)
    add_record(shelf, "grok", "g1", [("human", "sourdough starter question"), ("assistant", "feed it daily")])
    indexer.update(shelf.root, FakeEmbedder(), min_free_bytes=0)
    reg = identity.registry_path(shelf.root)
    other = identity.add_agent(reg, "mandated", tiers=("curated", "all"), scopes=("mandate:someone-else",))
    mine = identity.add_agent(reg, "owned", tiers=("curated", "all"), scopes=("robert",))
    client = service.create_app(registry_path=reg, shelf_root=shelf.root, embedder_factory=FakeEmbedder).test_client()
    body = {"query": "sourdough starter", "include_raw": True}
    assert post(client, other, body).get_json()["keyword"] == []                     # the record is scope robert
    assert post(client, mine, body).get_json()["keyword"]


def test_the_grant_caps_k_and_the_cap_is_not_negotiable(world):
    shelf, _, tokens, client = world
    assert post(client, tokens["cos"], {"query": "sourdough", "k": 3}).status_code == 200
    r = post(client, tokens["cos"], {"query": "sourdough", "k": 4})
    assert r.status_code == 400 and r.get_json() == {"error": "bad_k"}
    assert post(client, tokens["mc"], {"query": "sourdough", "k": 6}).status_code == 400
    assert post(client, tokens["mc"], {"query": "sourdough", "k": True}).status_code == 400


# ── revocation and rotation take effect on the next request ─────────────────

def test_revocation_applies_to_the_next_request_without_a_restart(world):
    shelf, reg, tokens, client = world
    assert post(client, tokens["mc"]).status_code == 200
    identity.revoke_agent(reg, "mc")
    assert post(client, tokens["mc"]).status_code == 401
    assert post(client, tokens["cos"]).status_code == 200                              # the other agent is unaffected
    assert [r["caller"]["agent"] for r in log_rows(shelf)] == ["mc", "cos"]            # nothing logged for the revoked call


def test_rotation_stops_the_old_token_and_starts_the_new_one(world):
    shelf, reg, tokens, client = world
    fresh = identity.rotate_agent(reg, "mc")
    assert fresh != tokens["mc"]
    assert post(client, tokens["mc"]).status_code == 401 and post(client, fresh).status_code == 200


def test_rotating_a_revoked_agent_is_the_owners_explicit_way_back(world):
    shelf, reg, tokens, client = world
    identity.revoke_agent(reg, "mc")
    fresh = identity.rotate_agent(reg, "mc")
    assert post(client, tokens["mc"]).status_code == 401 and post(client, fresh).status_code == 200


# ── request limits ───────────────────────────────────────────────────────────

def test_the_per_minute_cap_returns_429_for_that_agent_only(tmp_path):
    shelf = make_shelf(tmp_path)
    add_record(shelf, "grok", "g1", [("human", "sourdough"), ("assistant", "bread")])
    indexer.update(shelf.root, FakeEmbedder(), min_free_bytes=0)
    reg = identity.registry_path(shelf.root)
    slow, other = identity.add_agent(reg, "slow", rate_per_min=3), identity.add_agent(reg, "other")
    clock = {"t": 1000.0}
    client = service.create_app(registry_path=reg, shelf_root=shelf.root, embedder_factory=FakeEmbedder,
                                monotonic=lambda: clock["t"]).test_client()
    assert [post(client, slow).status_code for _ in range(3)] == [200, 200, 200]
    assert post(client, slow).status_code == 429 and post(client, slow).get_json() == {"error": "rate_limited"}
    assert post(client, other).status_code == 200
    clock["t"] += 61
    assert post(client, slow).status_code == 200


def test_unauthenticated_requests_do_not_consume_an_agents_rate_budget(tmp_path):
    shelf = make_shelf(tmp_path)
    add_record(shelf, "grok", "g1", [("human", "sourdough"), ("assistant", "bread")])
    indexer.update(shelf.root, FakeEmbedder(), min_free_bytes=0)
    reg = identity.registry_path(shelf.root)
    token = identity.add_agent(reg, "slow", rate_per_min=2)
    client = service.create_app(registry_path=reg, shelf_root=shelf.root, embedder_factory=FakeEmbedder).test_client()
    for _ in range(10):
        post(client, "w" * 43)
    assert post(client, token).status_code == 200


def test_body_size_json_and_media_type_are_checked_after_authentication(world):
    shelf, _, tokens, client = world
    auth = {"Authorization": f"Bearer {tokens['mc']}"}
    assert client.post("/v1/ask", data="x" * 5000, headers={**auth, "Content-Type": "application/json"}).status_code == 413
    assert client.post("/v1/ask", data="{oops", headers={**auth, "Content-Type": "application/json"}).get_json() == {"error": "invalid_json"}
    assert client.post("/v1/ask", data="query=sourdough", headers=auth).status_code == 415
    assert client.post("/v1/ask", json=["sourdough"], headers=auth).get_json() == {"error": "bad_arguments"}
    assert client.post("/v1/ask", data="x" * 5000, headers={"Content-Type": "application/json"}).status_code == 401   # not before auth


def test_an_empty_query_and_a_bad_date_are_400_with_fixed_codes(world):
    _, _, tokens, client = world
    assert post(client, tokens["mc"], {"query": "  "}).get_json() == {"error": "empty_query"}
    assert post(client, tokens["mc"], {"query": "x", "created_after": "yesterday"}).get_json() == {"error": "bad_date"}


def test_a_missing_index_is_503_not_a_leak(tmp_path):
    shelf = make_shelf(tmp_path)
    reg = identity.registry_path(shelf.root)
    token = identity.add_agent(reg, "mc")
    r = service.create_app(registry_path=reg, shelf_root=shelf.root, embedder_factory=FakeEmbedder).test_client().post(
        "/v1/ask", json={"query": "sourdough"}, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 503 and r.get_json() == {"error": "index_missing"}


def test_the_service_never_writes_a_token_or_a_query_to_its_log(world, capsys):
    shelf, _, tokens, client = world
    post(client, tokens["mc"], {"query": "a very private sourdough question"})
    post(client, "z" * 43)
    post(client, tokens["cos"], {"query": "x", "agent": "robert"})
    err = capsys.readouterr().err
    assert "private" not in err and all(t not in err for t in tokens.values()) and "zzzz" not in err
    assert "who=mc status=200" in err and "who=unknown status=401" in err


def test_health_needs_no_credential_and_says_nothing_else(world):
    _, _, _, client = world
    r = client.get("/v1/health")
    assert r.status_code == 200 and r.get_json() == {"ok": True}


# ── the owner's commands are terminal-only ───────────────────────────────────

def run_cli(reg, *argv, tty=True, yes=True):
    import io
    out = io.StringIO()
    code = agents_cli.main(["--registry", str(reg), *argv], out=out, is_tty=lambda: tty, confirm=lambda prompt: yes)
    return code, out.getvalue()


def test_no_agent_command_works_without_a_terminal(tmp_path):
    reg = tmp_path / "memory-ask" / "agents.json"
    for argv in (("add", "mc"), ("list",), ("revoke", "mc"), ("rotate", "mc")):
        code, text = run_cli(reg, *argv, tty=False)
        assert code == 3 and "not_interactive" in text
    assert not reg.exists()


def test_a_declined_confirmation_changes_nothing(tmp_path):
    reg = tmp_path / "memory-ask" / "agents.json"
    code, text = run_cli(reg, "add", "mc", yes=False)
    assert code == 3 and "declined" in text and not reg.exists()


def test_add_prints_the_token_once_and_list_never_does(tmp_path):
    reg = tmp_path / "memory-ask" / "agents.json"
    code, text = run_cli(reg, "add", "mc")
    token = text.strip().splitlines()[-1]
    assert code == 0 and len(token) >= 40 and identity.token_hash(token) in reg.read_text() and token not in reg.read_text()
    code, text = run_cli(reg, "list")
    assert code == 0 and token not in text and identity.token_hash(token) not in text and '"agent": "mc"' in text
    assert '"tiers": [\n   "curated"\n  ]' in text                                    # raw is off unless asked for


def test_raw_and_scope_flags_are_explicit(tmp_path):
    reg = tmp_path / "memory-ask" / "agents.json"
    run_cli(reg, "add", "cos", "--raw", "--scope", "robert", "--scope", "mandate:abc", "--max-k", "4")
    row = json.loads(reg.read_text())["agents"]["cos"]
    assert row["tiers"] == ["curated", "all"] and row["scopes"] == ["robert", "mandate:abc"] and row["max_k"] == 4


def test_revoke_and_rotate_through_the_cli(tmp_path):
    reg = tmp_path / "memory-ask" / "agents.json"
    first = run_cli(reg, "add", "mc")[1].strip().splitlines()[-1]
    assert run_cli(reg, "revoke", "mc")[0] == 0
    assert identity.authenticate(identity.load(reg), first) == (None, "revoked")
    second = run_cli(reg, "rotate", "mc")[1].strip().splitlines()[-1]
    assert identity.authenticate(identity.load(reg), second)[1] == "ok" and identity.authenticate(identity.load(reg), first)[0] is None
    assert run_cli(reg, "revoke", "nobody")[0] == 1


def test_authenticate_rejects_non_strings_and_odd_lengths():
    for token in (None, 5, b"x" * 40, "", "a" * 15, "a" * 257):
        assert identity.authenticate({}, token) == (None, "unknown")


def test_mc_and_cos_get_identical_results_through_the_service_apart_from_who_asked(world):
    """The neutrality Codex said the function-level test did not show: same query, two agents, over the real service boundary."""
    shelf, reg, tokens, client = world
    third = identity.add_agent(reg, "twin", tiers=("curated", "all"), max_k=3)
    a = post(client, tokens["cos"], {"query": "sourdough starter", "include_raw": True, "k": 3}).get_json()
    b = post(client, third, {"query": "sourdough starter", "include_raw": True, "k": 3}).get_json()

    def strip(r):
        return {k: v for k, v in r.items() if k not in ("query_id", "caller", "status")}
    assert strip(a) == strip(b) and a["keyword"]
    assert a["caller"]["agent"] == "cos" and b["caller"]["agent"] == "twin"


# ── Codex review of 6727b937: an omitted k, and the body limit while reading ────────────────────────────────────────

def test_an_omitted_k_is_capped_by_the_grant_not_the_global_default(world):
    """cos has max_k 3. Omitting k used to run with the global default 5 and bypass the smaller cap."""
    shelf, _, tokens, client = world
    for i in range(6):
        add_record(shelf, "grok", f"extra{i}", [("human", f"sourdough starter question {i}"), ("assistant", f"starter answer {i}")])
    indexer.update(shelf.root, FakeEmbedder(), min_free_bytes=0)
    r = post(client, tokens["cos"], {"query": "sourdough starter question", "include_raw": True})
    body = r.get_json()
    assert r.status_code == 200 and len(body["keyword"]) <= 3 and len(body["meaning"]) <= 3


@pytest.mark.parametrize("max_k,expected_cap", [(1, 1), (2, 2), (5, 5), (9, 5)])
def test_the_effective_k_is_the_smaller_of_the_grant_and_the_default(tmp_path, max_k, expected_cap):
    shelf = make_shelf(tmp_path)
    for i in range(8):
        add_record(shelf, "grok", f"g{i}", [("human", f"sourdough starter question {i}"), ("assistant", f"starter answer {i}")])
    indexer.update(shelf.root, FakeEmbedder(), min_free_bytes=0)
    reg = identity.registry_path(shelf.root)
    token = identity.add_agent(reg, "capped", tiers=("curated", "all"), max_k=max_k)
    client = service.create_app(registry_path=reg, shelf_root=shelf.root, embedder_factory=FakeEmbedder).test_client()
    body = post(client, token, {"query": "sourdough starter question", "include_raw": True}).get_json()
    assert max(len(body["keyword"]), len(body["meaning"])) == expected_cap


class CountingStream:
    """A request body that never ends and counts how much the service pulled from it."""

    def __init__(self, total=50 * 1024 * 1024):
        self.total, self.read_bytes = total, 0

    def read(self, n=-1):
        n = self.total - self.read_bytes if n is None or n < 0 else min(n, self.total - self.read_bytes)
        self.read_bytes += n
        return b"x" * n

    def readline(self, n=-1):
        return self.read(min(n, 8192) if n and n > 0 else 8192)

    def readinto(self, buf):
        data = self.read(len(buf))
        buf[: len(data)] = data
        return len(data)


def _open(client, stream, *, content_length, token=None):
    """POST with a body the service pulls from ``stream``: ``content_length`` is what the client claims ("" = chunked, none sent)."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.open("/v1/ask", method="POST", content_type="application/json", headers=headers,
                       environ_overrides={"wsgi.input": stream, "wsgi.input_terminated": True, "CONTENT_LENGTH": content_length})


def test_the_body_limit_applies_while_reading_not_after_loading_it_all(world):
    shelf, _, tokens, client = world
    stream = CountingStream()
    response = _open(client, stream, content_length="", token=tokens["mc"])           # chunked: no Content-Length to trust
    assert response.status_code == 413 and response.get_json() == {"error": "too_large"}
    assert 0 < stream.read_bytes <= service.MAX_BODY + 1


def test_a_lying_small_content_length_cannot_make_the_service_read_more(world):
    shelf, _, tokens, client = world
    stream = CountingStream()
    _open(client, stream, content_length="100", token=tokens["mc"])
    assert stream.read_bytes <= service.MAX_BODY + 1


def test_a_huge_declared_length_is_refused_without_reading_anything(world):
    shelf, _, tokens, client = world
    stream = CountingStream()
    assert _open(client, stream, content_length=str(50 * 1024 * 1024), token=tokens["mc"]).status_code == 413
    assert stream.read_bytes == 0


def test_an_unauthenticated_giant_body_is_never_read_at_all(world):
    shelf, _, tokens, client = world
    stream = CountingStream()
    response = _open(client, stream, content_length="")
    assert response.status_code == 401 and stream.read_bytes == 0
