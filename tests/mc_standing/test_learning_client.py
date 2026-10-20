"""Eligibility-snapshot HTTP adapter (harness §14.6) against a fake transport, and end to end
through the consolidation job."""
import io
import json
import urllib.error

import pytest

from core.mc_standing import consolidation as c
from core.mc_standing.learning_client import EligibilityClient, LearningUnavailable
from core.mc_standing.spool import Spool
from tests.mc_standing.test_consolidation import VERSIONS, Admission, Claims, Ingress, lesson


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def page(sources, **over):
    doc = {"snapshot_id": "snap-7", "sources": sources, "learning_refs": ["lesson-1"],
           "policy_version": "extract-p1", "next_cursor": None, "coverage": "complete"}
    doc.update(over)
    return doc


def src(i):
    return {"id": f"stmt-{i}", "edition": "e1", "digest": f"{i:064x}", "seq": i}


class Server:
    """Fake harness: serves pages after the cursor and source texts; records requests."""

    def __init__(self, n=3, revoked=(), missing=(), page_override=None, status=None):
        self.sources = [src(i) for i in range(1, n + 1)]
        self.revoked, self.missing = set(revoked), set(missing)
        self.page_override, self.status, self.requests = page_override, status, []

    def __call__(self, req, timeout):
        self.requests.append((req.full_url, req.get_header("Authorization")))
        if self.status:
            raise urllib.error.HTTPError(req.full_url, self.status, "x", {}, io.BytesIO(b"{}"))
        url = req.full_url.split("http://h", 1)[1]
        if url.startswith("/internal/mc/v1/learning/eligible"):
            if self.page_override is not None:
                return Resp(json.dumps(self.page_override).encode())
            q = dict(p.split("=") for p in url.split("?", 1)[1].split("&"))
            cursor, limit = int(q["cursor"]) if "cursor" in q else None, int(q["limit"])
            rest = [s for s in self.sources if cursor is None or s["seq"] > cursor][:limit]
            return Resp(json.dumps(page(rest)).encode())
        sid = url.rsplit("/", 1)[1]
        if sid in self.revoked:
            raise urllib.error.HTTPError(req.full_url, 410, "gone", {}, io.BytesIO(b"{}"))
        if sid in self.missing:
            raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, io.BytesIO(b"{}"))
        return Resp(json.dumps({"text": f"statement {sid}"}).encode())


def client(server):
    return EligibilityClient("http://h", lambda: "tok-" + "x" * 40, opener=server)


def test_eligible_sends_cursor_and_limit_and_validates_the_page():
    server = Server(n=12)
    doc = client(server).eligible(None, 10)
    assert [s["seq"] for s in doc["sources"]] == list(range(1, 11))
    client(server).eligible(10, 10)
    assert "cursor=10" in server.requests[-1][0] and "limit=10" in server.requests[-1][0]
    assert server.requests[-1][1].startswith("Bearer tok-")


@pytest.mark.parametrize("bad", [
    page([src(1)], extra="x"), page([src(2), src(1)]), page([dict(src(1), digest="nothex")]),
    page([dict(src(1), seq=True)]), page([dict(src(1), text="leak")]), page([src(i) for i in range(1, 12)]),
    page([src(1)], coverage="unavailable"), page([src(1)], learning_refs=["../x"])])
def test_malformed_pages_fail_closed(bad):
    with pytest.raises(LearningUnavailable):
        client(Server(page_override=bad)).eligible(None, 10)


def test_page_at_or_before_the_cursor_is_refused():
    with pytest.raises(LearningUnavailable, match="commit_order"):
        client(Server(page_override=page([src(5)]))).eligible(5, 10)


def test_read_maps_410_to_revoked_and_404_to_missing():
    server = Server(revoked={"stmt-2"}, missing={"stmt-3"})
    cl = client(server)
    assert cl.read("snap-7", "stmt-1") == "statement stmt-1"
    with pytest.raises(c.Revoked):
        cl.read("snap-7", "stmt-2")
    assert cl.read("snap-7", "stmt-3") is None
    with pytest.raises(ValueError):
        cl.read("snap-7", "../etc")


def test_unreachable_or_5xx_is_unavailable():
    with pytest.raises(LearningUnavailable):
        client(Server(status=503)).eligible(None, 10)

    def down(req, timeout):
        raise urllib.error.URLError(ConnectionRefusedError())
    with pytest.raises(LearningUnavailable):
        EligibilityClient("http://h", lambda: "t", opener=down).eligible(None, 10)


def _run(tmp_path, server, **kw):
    spool = kw.pop("spool", None) or Spool(tmp_path / "state")
    out = c.run_consolidation(spool=spool, eligibility=client(server), claims=kw.pop("claims", Claims()),
                              extractor=kw.pop("extractor", lesson()), admission=Admission(),
                              ingress=kw.pop("ingress", Ingress()), principal="robert", environment="prod",
                              policy_hash="a" * 64, versions=VERSIONS, now_iso="2026-10-21T09:40:00Z",
                              enabled=True)
    return spool, out


def test_end_to_end_through_the_adapter(tmp_path):
    server, ing, claims = Server(n=3, revoked={"stmt-2"}), Ingress(), Claims()
    spool, out = _run(tmp_path, server, ingress=ing, claims=claims)
    assert out == {"state": "delivered", "events": 1, "revoked": 1}
    assert [r["id"] for r in ing.posts[0]["source_refs"]] == ["stmt-1", "stmt-3"]
    _, out = _run(tmp_path, server, spool=spool, claims=claims, ingress=ing)
    assert out["state"] == "nothing_new" and "cursor=3" in server.requests[-1][0]


def test_harness_unavailable_defers_without_claiming(tmp_path):
    claims = Claims()
    _, out = _run(tmp_path, Server(status=503), claims=claims)
    assert out == {"state": "deferred", "reason": "eligibility_unavailable"} and claims.log == []
