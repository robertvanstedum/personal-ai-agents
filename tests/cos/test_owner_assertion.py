"""F3 (partnership v1.1 B0-2): a CoS owner write is the owner's only with the portal's signed assertion for that exact
request, spent once. Forwarded headers alone never are. (The autouse fixture in conftest.py signs requests that carry
X-Test-Sign-Owner exactly as the portal does.)"""
import threading

from flask import Flask, jsonify

from core import portal_assertion as PA
from domains.cos import owner_assertion as OA
from conftest import OWNER_SIGNING_KEY


def app():
    a = Flask("owner-assertion-test")

    @a.route("/w", methods=["POST"])
    def write():
        first = OA.verified_owner_write()
        return jsonify(owner=first, again=OA.verified_owner_write())     # a second check in one request reuses it
    return a.test_client()


def sign(**over):
    claims = dict(audience="cos-owner", subject="owner:7", method="POST", target="/w", body=b'{"a":1}', env="test")
    claims.update(over)
    return PA.sign(OWNER_SIGNING_KEY, **claims)


def post(client, headers):
    return client.post("/w", data=b'{"a":1}', headers={"Content-Type": "application/json", **headers}).get_json()


def test_a_signed_request_is_the_owners_and_a_second_check_in_it_reuses_the_answer():
    got = post(app(), {"X-Test-Sign-Owner": "7"})
    assert got == {"owner": "7", "again": "7"}


def test_forwarded_headers_alone_are_never_the_owners():
    forged = {"X-Minimoi-Auth-Id": "7", "X-Minimoi-User-Tier": "owner", "X-Minimoi-Voice-Write-Verified": "1",
              "X-Test-Unsigned": "1"}
    assert post(app(), forged)["owner"] is None


def test_a_replayed_assertion_is_refused():
    client, header = app(), sign()
    assert post(client, {OA.HEADER: header})["owner"] == "7"
    assert post(client, {OA.HEADER: header, "X-Test-Unsigned": "1"})["owner"] is None


def test_an_assertion_for_another_request_audience_environment_or_key_is_refused():
    client = app()
    post = lambda c, h: globals()["post"](c, {**h, "X-Test-Unsigned": "1"})
    for bad in (sign(target="/other"), sign(body=b"{}"), sign(method="PUT"), sign(audience="records"),
                sign(env="production"), sign(subject="robert"),
                PA.sign(("test-kid", "another-secret-0123456789abcdefghij"), audience="cos-owner", subject="owner:7",
                        method="POST", target="/w", body=b'{"a":1}', env="test")):
        assert post(client, {OA.HEADER: bad})["owner"] is None, bad


def test_an_expired_assertion_is_refused():
    assert post(app(), {OA.HEADER: sign(now=1_000_000), "X-Test-Unsigned": "1"})["owner"] is None


def test_without_its_key_environment_or_nonce_store_every_owner_write_is_refused(monkeypatch):
    for name in ("COS_OWNER_ASSERTION_KEY_FILE", "COS_OWNER_ASSERTION_ENV", "COS_OWNER_ASSERTION_NONCE_FILE"):
        with monkeypatch.context() as m:
            m.delenv(name)
            assert post(app(), {"X-Test-Sign-Owner": "7"})["owner"] is None, name


def test_one_nonce_spent_by_many_at_once_is_accepted_exactly_once(tmp_path):
    spend = PA.NonceFile(tmp_path / "nonces.sqlite3")
    start, results = threading.Barrier(8), []

    def go():
        start.wait()
        results.append(spend("n" * 24, 10**12))
    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(results) == [False] * 7 + [True]


def test_spent_nonces_survive_a_restart(tmp_path):
    assert PA.NonceFile(tmp_path / "n.sqlite3")("n" * 24, 10**12)
    assert not PA.NonceFile(tmp_path / "n.sqlite3")("n" * 24, 10**12)


def test_an_unsigned_caller_never_makes_cos_read_its_body(monkeypatch):
    read = []
    from flask import Request
    original = Request.get_data
    monkeypatch.setattr(Request, "get_data", lambda self, *a, **k: read.append(1) or original(self, *a, **k))
    a = Flask("body-test")

    @a.route("/w", methods=["POST"])
    def write():
        return jsonify(owner=OA.verified_owner_write())
    client = a.test_client()
    assert client.post("/w", data=b"x" * 5_000_000, headers={OA.HEADER: sign(), "X-Test-Unsigned": "1"}).get_json()["owner"] is None
    assert read == []                                                   # garbage MAC: body never read
    assert client.post("/w", data=b"x" * 5_000_000,
                       headers={OA.HEADER: sign(body=b"x" * 5_000_000), "X-Test-Unsigned": "1"}).get_json()["owner"] is None
    assert read == []                                                   # valid MAC, over the cap: never read


def test_a_failing_nonce_store_refuses_instead_of_erroring(monkeypatch, tmp_path):
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    blocked.chmod(0o500)
    monkeypatch.setenv("COS_OWNER_ASSERTION_NONCE_FILE", str(blocked / "nonces.sqlite3"))
    try:
        assert post(app(), {"X-Test-Sign-Owner": "7"})["owner"] is None
    finally:
        blocked.chmod(0o700)


def test_the_answer_never_outlives_its_request_even_under_an_outer_app_context():
    a = Flask("ctx-test")

    @a.route("/w", methods=["POST"])
    def write():
        return jsonify(owner=OA.verified_owner_write())
    with a.app_context():
        client = a.test_client()
        assert post(client, {"X-Test-Sign-Owner": "7"})["owner"] == "7"
        assert post(client, {"X-Test-Unsigned": "1"})["owner"] is None
