"""#660, the point of the lane: a question worded differently from a note finds it, for CoS and for MC.

Through the real Ask service (``core.memory_ask.service``) with each partner's own grant: CoS on the full index, MC on its
career-filtered index built from the full one as donor (``mc_index``). The embedder is the deterministic FakeEmbedder carrying the
pinned digest: "yeast" and "loaf" share no word with the note but land on the same concept, which is what a real embedding does."""
from core.memory_ask import identity, service
from core.memory_index import career_filter, indexer, mc_index
from core.memory_index.embedder import PIN_DIGEST_FULL
from tests.memory_index.helpers import FakeEmbedder, add_record, make_shelf

NOTE = [("human", "How do I get my sourdough to rise properly?"),
        ("assistant", "Feed the starter twice a day and proof the dough somewhere warm overnight.")]
QUESTION = "yeast loaf"                   # no word in common with the note


def pinned():
    return FakeEmbedder(digest=PIN_DIGEST_FULL)


def world(tmp_path):
    shelf = make_shelf(tmp_path)
    note = add_record(shelf, "grok", "g-bread", NOTE)
    add_record(shelf, "grok", "g-sky", [("human", "Which telescope shows Jupiter's moons?"), ("assistant", "A 6 inch Dobsonian.")])
    full, mc = tmp_path / "index" / "memory.sqlite", tmp_path / "vault-mc" / "memory-mc.sqlite"
    full.parent.mkdir()
    assert indexer.update(shelf.root, pinned(), index_path=full, min_free_bytes=0)["code"] == "ok"
    out = mc_index.build(shelf.root, mc, career_filter.load(), release="r1", embedder=pinned(), donor_path=full)
    assert out["code"] == "ok" and out["kind"] == "meaning"
    return shelf, note, full, mc


def ask(tmp_path, shelf, index, name, **kw):
    reg = tmp_path / "ask" / f"agents-{name}.json"
    token = identity.add_agent(reg, name, tiers=("curated", "all"))
    app = service.create_app(registry_path=reg, shelf_root=shelf.root, index_path=index, embedder_factory=pinned,
                             log_path=tmp_path / f"{name}.jsonl", **kw).test_client()
    reply = app.post("/v1/ask", json={"query": QUESTION, "include_raw": True}, headers={"Authorization": f"Bearer {token}"})
    assert reply.status_code == 200, reply.get_json()
    return reply.get_json()


def test_cos_finds_a_note_worded_differently(tmp_path):
    shelf, note, full, _ = world(tmp_path)
    got = ask(tmp_path, shelf, full, "cos")
    assert got["keyword"] == []                                            # the words never match
    assert got["status"]["meaning"] == "ok"
    assert got["meaning"] and got["meaning"][0]["record_id"] == note     # the meaning does


def test_mc_finds_it_through_its_own_filtered_index(tmp_path):
    shelf, note, _, mc = world(tmp_path)
    got = ask(tmp_path, shelf, mc, "mc", require_profile="mc")
    assert got["keyword"] == [] and got["status"]["meaning"] == "ok"
    assert got["meaning"] and got["meaning"][0]["record_id"] == note


def test_mc_s_tool_shapes_the_meaning_hit_for_mc(tmp_path):
    from core.mc_tools.vault_search import shape
    shelf, note, _, mc = world(tmp_path)
    message, data = shape(ask(tmp_path, shelf, mc, "mc", require_profile="mc"))
    assert data["count"] >= 1 and data["results"][0]["list"] == "meaning" and data["meaning"] == "ok"
    assert "sourdough" in data["results"][0]["passage"] or "dough" in data["results"][0]["passage"]
