"""A production host serves keyword search from a copied index without sqlite-vec (17 Oct 2026).

Production images carry no sqlite-vec and no embedder. Reading an index there must still give the keyword list; the
meaning list is empty with status ``vector_unavailable`` and no embedder is ever asked. Creating or rebuilding an index
still needs the extension. Synthetic records only.
"""
import subprocess
import sys
from pathlib import Path

import pytest

from core.memory_index import indexer, policy, search, store
from .helpers import FakeEmbedder, add_record, make_shelf

RAW = policy.Caller(tier="all")
ROOT = Path(__file__).resolve().parents[2]


class NeverCalled(FakeEmbedder):
    def identity(self):
        raise AssertionError("a keyword-only host must not ask an embedder")


@pytest.fixture
def built(tmp_path):
    shelf = make_shelf(tmp_path)
    rid = add_record(shelf, "grok", "topic", [("assistant", "The integration test failed because the gateway restarted.")])
    indexer.update(shelf.root, FakeEmbedder(), min_free_bytes=0)          # built on a host that has sqlite-vec
    return shelf, rid


def test_keyword_search_works_without_sqlite_vec_and_never_asks_an_embedder(built, monkeypatch):
    shelf, rid = built
    monkeypatch.setattr(store, "sqlite_vec", None)
    monkeypatch.setattr(search, "sqlite_vec", None)
    r = search.search("integration gateway", shelf_root=shelf.root, embedder=NeverCalled(), caller=RAW, k=2)
    assert [h["record_id"] for h in r["keyword"]][:1] == [rid]
    assert r["meaning"] == [] and r["status"]["meaning"] == search.VECTOR_UNAVAILABLE


def test_an_index_cannot_be_created_or_written_without_sqlite_vec(built, monkeypatch, tmp_path):
    shelf, _ = built
    monkeypatch.setattr(store, "sqlite_vec", None)
    with pytest.raises(store.IndexIncompatible):
        store.connect(tmp_path / "new.sqlite", create=True)
    with pytest.raises(store.IndexIncompatible):
        store.connect(indexer.default_index_path(shelf.root))
    store.connect(indexer.default_index_path(shelf.root), readonly=True).close()


def test_the_modules_import_when_sqlite_vec_is_not_installed():
    code = ("import sys; sys.modules['sqlite_vec'] = None\n"
            "from core.memory_index import store, search, indexer\n"
            "from core.memory_ask import service\n"
            "assert store.sqlite_vec is None and search.sqlite_vec is None and indexer.sqlite_vec is None\n"
            "print('ok')")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stdout.strip() == "ok", out.stderr
