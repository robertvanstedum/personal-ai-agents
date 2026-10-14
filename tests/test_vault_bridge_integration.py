"""Actual HTTP Ask/grant boundary, synthetic shelf and deterministic embedding.

Runs when the Vault package/test fixtures are assembled. This is not a live
tunnel, real shelf or provider/model acceptance test.
"""
import threading

import pytest
from flask import Flask
from werkzeug.serving import make_server

from minimoi_portal import vault_bridge as bridge


def test_http_ask_preserves_live_scope_tier_and_revocation(tmp_path, monkeypatch):
    service = pytest.importorskip("core.memory_ask.service")
    identity = pytest.importorskip("core.memory_ask.identity")
    indexer = pytest.importorskip("core.memory_index.indexer")
    fixtures = pytest.importorskip("tests.memory_index.helpers")
    shelf = fixtures.make_shelf(tmp_path)
    rid = fixtures.add_record(shelf, "grok", "bridge-test", [
        ("human", "How should we make sourdough bread rise?"),
        ("assistant", "Feed the starter twice a day.")])
    fixtures.designate(shelf, rid)
    assert indexer.update(shelf.root, fixtures.FakeEmbedder(), min_free_bytes=0)["code"] == "ok"
    registry = identity.registry_path(shelf.root)
    token = identity.add_agent(registry, "mc", tiers=("curated",), scopes=("robert",))
    app = service.create_app(registry_path=registry, shelf_root=shelf.root, embedder_factory=fixtures.FakeEmbedder)
    server = make_server("127.0.0.1", 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # Ephemeral port avoids interfering with any real service on8771.
        backend = f"http://127.0.0.1:{server.server_port}"
        monkeypatch.setattr(bridge, "BACKENDS", frozenset({backend}))
        portal = Flask(__name__)
        assert bridge.install_if_dev(portal, base_url=bridge.ORIGIN,
            environ={"MINIMOI_VAULT_BRIDGE": "1", "MINIMOI_VAULT_BRIDGE_BACKEND": backend})
        client = portal.test_client()
        def ask(body, bearer=token):
            return client.post("https://dev.minimoi.ai/internal/vault/v1/ask", json=body,
                headers={"Authorization": "Bearer " + bearer, "X-Minimoi-User-Tier": "owner"})
        response = ask({"query": "sourdough starter"})
        assert response.status_code == 200
        assert response.json["caller"] == {"agent": "mc", "entry_point": "tool"}
        assert response.json["keyword"]
        assert ask({"query": "sourdough", "include_raw": True}).status_code == 403
        assert ask({"query": "sourdough", "scopes": ["mandate:private"]}).status_code == 400
        assert ask({"query": "sourdough"}, "z" * 43).status_code == 401
        identity.revoke_agent(registry, "mc")
        revoked = ask({"query": "sourdough"})
        assert revoked.status_code == 401 and revoked.json == {"error": "unauthorized"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
