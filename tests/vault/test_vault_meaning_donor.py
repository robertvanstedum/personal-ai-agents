"""The one-time copy of the Mac's vectors (#660, the owner's decision of 26 Nov): the seed borrows the Mac's own meaning index, taken from
the latest published production snapshot, instead of embedding every chunk on the server. ``seed-donor`` fetches only the index object
(chain, hashes, root-only identity), checks it is the pinned embedder's with one vector per chunk, and leaves it beside the seed; each seed
slice takes its vectors, re-embeds a sample with the server's embedder (must agree, cosine >= 0.999), and drops the copy once complete.
Synthetic and local, on the ``test_vault_apply`` fixtures with the pinned FakeEmbedder; no Ollama, no S3, no docker."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import pytest

from core.memory_backup import backup as B
from core.memory_backup import production as P
from core.memory_index import indexer
from core.memory_index.embedder import PIN_DIGEST_FULL
from core.memory_sync import apply as A
from tests.memory_index.helpers import FakeEmbedder, add_record
from tests.vault.test_vault_apply import apply, changes_on_mac, cli, current, load, send, w  # noqa: F401
from tests.vault.test_vault_apply_meaning import NIGHT, Pinned, meaning_ids, seed_dir, seed_slice, serving_index

DAY = datetime(2026, 11, 26, 18, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def no_real_deadline(monkeypatch):
    monkeypatch.setattr(A, "window_deadline", lambda now: None)


class Skewed(FakeEmbedder):
    """Carries the pinned digest but makes different vectors: what a wrong copy would look like."""

    def __init__(self):
        super().__init__(digest=PIN_DIGEST_FULL)

    def _embed(self, texts):
        out = super()._embed(texts)
        for v in out:
            v[0], v[1] = 0.0, 5.0
        return out


def publish_mac_index(w, emb, *, name="mac-meaning"):
    """The Mac's nightly meaning index, published as the production snapshot (the existing publish path; production keeps serving)."""
    index = w.tmp / name / "memory.sqlite"
    assert indexer.update(w.mac.root, emb, index_path=index, min_free_bytes=None)["code"] == "ok"
    P.run_publish(shelf_root=str(w.mac.root), index_path=str(index), dest=w.dest, recipient=w.recipient, workdir=str(w.tmp / "pubwork2"),
                  age_bin=w.age, now=datetime(2026, 11, 26, 1, 0, tzinfo=timezone.utc), enforce_window=False)
    return index


def fetch_donor(w, **kw):
    args = dict(dest=w.dest, vault_root=str(w.vault), identity_spec=w.identity, age_bin=w.age, owner_uid=os.geteuid(),
                reader_uid=os.geteuid(), reader_gid=os.getegid(), require_root=False, now=lambda: NIGHT)
    args.update(kw)
    return A.run_seed_donor(**args)


def test_the_copy_lands_beside_the_seed_and_nothing_serving_changes(w):
    publish_mac_index(w, Pinned())
    serving = current(w)
    out = fetch_donor(w)
    assert out["code"] == "donor_ready" and out["chunks"] > 0
    assert (seed_dir(w) / A.DONOR_NAME).stat().st_mode & 0o777 == 0o600
    doc = json.loads((seed_dir(w) / A.DONOR_DOC).read_text())
    assert doc["embedder_digest"] == PIN_DIGEST_FULL and doc["chunks"] == out["chunks"]
    assert current(w) == serving and A.index_kind(serving_index(w)) == ("keyword", None)
    assert not (seed_dir(w) / P.INDEX_NAME).exists()                       # the seed index is the seed's to build
    assert fetch_donor(w)["code"] == "donor_present"                       # idempotent
    assert A.plan_apply(vault_root=str(w.vault))["meaning_donor"]["chunks"] == out["chunks"]


def test_the_seed_borrows_the_copy_spot_checks_it_and_apply_promotes(w):
    publish_mac_index(w, Pinned())
    assert fetch_donor(w)["code"] == "donor_ready"
    emb = Pinned()
    out = seed_slice(w, emb)
    assert out["code"] == "ok" and out["complete"] and out["donor"]
    assert out["donor_vectors"] > 0 and out.get("embedded_chunks", 0) == 0     # every chunk's vector came from the Mac
    assert out["donor_checked"] == min(out["donor_vectors"], indexer.DONOR_CHECK_FIRST)
    assert len(emb.seen) == out["donor_checked"]                               # only the spot check reached the server's embedder
    doc = json.loads((seed_dir(w) / A.SEED_DOC).read_text())
    assert doc["donor"] == {"vectors": out["donor_vectors"], "checked": out["donor_checked"], "min_cosine": "0.999"}
    assert not (seed_dir(w) / A.DONOR_NAME).exists() and not (seed_dir(w) / A.DONOR_DOC).exists()   # dropped once complete
    res = apply(w, index_runner=A.HostRunner(Pinned()))
    assert res["seed_promoted"] and res["index_kind"] == "meaning"
    assert w.ids["a"] in meaning_ids(w, "alpha original kumquat", Pinned())


def test_records_newer_than_the_copy_are_embedded_on_the_server(w):
    publish_mac_index(w, Pinned())
    assert fetch_donor(w)["code"] == "donor_ready"
    rid = add_record(w.mac, "grok", "g-new", [("human", "delta question"), ("assistant", "delta answer quince")])
    send(w, [rid])
    assert apply(w)["built"]                                                  # keyword apply brings the new record to production
    emb = Pinned()
    out = seed_slice(w, emb)
    assert out["code"] == "ok" and out["embedded_chunks"] > 0 and out["donor_vectors"] > 0


def test_a_copy_that_disagrees_with_the_server_is_discarded_with_everything_built_from_it(w):
    publish_mac_index(w, Skewed())
    assert fetch_donor(w)["code"] == "donor_ready"
    with pytest.raises(B.BackupError) as e:
        seed_slice(w, Pinned())
    assert e.value.code == "seed_failed:donor_mismatch"
    assert not seed_dir(w).exists()                                           # no lent vector survives
    out = seed_slice(w, Pinned())                                             # the next slice embeds on the server, as before the copy
    assert out["code"] == "ok" and not out["donor"] and out["embedded_chunks"] > 0


def test_a_published_keyword_index_is_refused_as_a_copy(w):
    # The fixture's own published snapshot is keyword-only: nothing to copy.
    with pytest.raises(B.BackupError) as e:
        fetch_donor(w)
    assert e.value.code == "donor_not_pinned" and not (seed_dir(w) / A.DONOR_NAME).exists()


def test_a_copy_made_with_another_embedder_is_refused(w):
    publish_mac_index(w, FakeEmbedder(digest="0a109f422b47" + "e" * 52))
    with pytest.raises(B.BackupError) as e:
        fetch_donor(w)
    assert e.value.code == "donor_not_pinned"


def test_a_tampered_ciphertext_is_refused(w):
    publish_mac_index(w, Pinned())
    pointer = json.loads((w.destdir / "production" / "latest.json").read_text())
    obj = w.destdir / "production" / pointer["day"] / pointer["attempt"] / P.INDEX_OBJECT
    os.chmod(obj, 0o600)
    with open(obj, "r+b") as f:
        f.seek(100)
        f.write(b"\0\0\0\0")
    with pytest.raises(B.BackupError) as e:
        fetch_donor(w)
    assert e.value.code == "ciphertext_mismatch" and not seed_dir(w).exists()


def test_the_copy_waits_for_the_window_and_is_not_needed_once_meaning_serves(w):
    publish_mac_index(w, Pinned())
    assert fetch_donor(w, now=lambda: DAY) == {"code": "outside_window"}
    assert fetch_donor(w, now=lambda: DAY, allow_outside_window=True)["code"] == "donor_ready"
    seed_slice(w, Pinned())
    apply(w, index_runner=A.HostRunner(Pinned()))
    assert fetch_donor(w)["code"] == "seed_not_needed"


def test_the_docker_seed_passes_the_copy_read_from_the_seed_folder(tmp_path):
    r = A.DockerRunner("sha256:" + "a" * 64, 0, 0, embedder_url="http://vault-embedder:11434", network="minimoi-vault-embed")
    argv = r.seed_argv(tmp_path / "shelf", tmp_path / "seed", 420, None, donor=True)
    assert argv[-2:] == ["--donor", f"/index/{A.DONOR_NAME}"] and "minimoi-vault-embed" in argv
    assert "--donor" not in r.seed_argv(tmp_path / "shelf", tmp_path / "seed", 420, None)


def test_the_cli_seed_donor(w, tmp_path, capsys):
    publish_mac_index(w, Pinned())
    va = load("vault_apply")
    assert va.main(cli(w, tmp_path / "jobs") + ["seed-donor"]) == 2
    rc = va.main(cli(w, tmp_path / "jobs") + ["seed-donor", "--execute", "--allow-outside-window"])
    assert rc == 0 and "donor_ready" in capsys.readouterr().out
    status = json.loads((tmp_path / "jobs" / "vault-meaning-seed.json").read_text())
    assert status["results"]["seed_donor"] == "donor_ready"
