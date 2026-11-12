"""Production CoS conversations reach the production Vault automatically: the whole trip, end to end, on fixtures (#509).

Every job is the real one; nothing is mocked between them (the ``test_mc_e2e`` pattern). Production is a synthetic canonical CoS history;
EC2's ``vault-cos-export`` reads it in-process; the Mac's ``vault-cos-pull`` verifies, places and acknowledges, then the approval-gated
``minimoi-cos`` capture writes records on the Mac shelf; ``vault-sync`` sends them; ``vault-apply`` installs them in production (seeded by
r3's snapshot transport); and ``vault-cos-export`` reconciles against the serving Vault. ``file://`` stands in for S3; the clock is
injected; age is the pinned real binary with a throwaway identity when installed, else the transport tests' stand-in.

Scenarios: the trip (a private conversation's words never travel anywhere, nor another environment's), a new turn adds an edition, a pull
that dies before its ack, and a gap (a placed file lost before capture) that reconciliation re-queues until the Vault holds it. MC's
own trip (``test_mc_e2e``) runs alongside unchanged."""
from __future__ import annotations

import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.memory_backup import production as P
from core.memory_index import indexer, policy, search
from core.memory_shelf import approvals, watchers
from core.memory_shelf import config as shelf_config
from core.memory_shelf.shelf import Shelf
from core.memory_sync import apply as A
from core.memory_sync import cos_export as CX
from core.memory_sync import cos_pull as CP
from core.memory_sync import sync as S
from core.memory_sync import versions as V
from tests.memory_backup.test_production_transport import make_age
from tests.memory_index.helpers import add_record
from tests.memory_shelf.test_mc_capture import OWNER
from tests.vault.cos_history import History

ROOT = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 11, 11, 20, 0, tzinfo=timezone.utc)          # 14:00 Chicago: daytime, the light bound applies
PRIVATE = ("PRIVATE-SECRET", "NULLPRIV-SECRET", "DEV-SECRET")


class NoModel:
    def release(self):
        pass

    def check_pin(self):
        raise AssertionError("no model calls permitted")


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


class Trip:
    def __init__(self, tmp: Path):
        self.tmp, self.clock = tmp, Clock(T0)
        self.history = History(tmp / "prod" / "cos" / "conversations" / "history")
        real = Path.home() / "minimoi-staging" / "tools" / "age-v1.3.2" / "age"
        if real.is_file() and real.with_name("age-keygen").is_file():
            spec = importlib.util.spec_from_file_location("sync_e2e_fixture", ROOT / "scripts/vault/sync_e2e_fixture.py")
            E = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(E)
            self.age = str(real)
            self.recipient, self.identity = E.make_identity(tmp, str(real.with_name("age-keygen")))
        else:
            self.age, self.recipient, self.identity = make_age(tmp)
        self.destdir = tmp / "s3"
        self.dest = f"file://{self.destdir}"
        self.mac = tmp / "mac"
        self.shelf_root = self.mac / "data" / "memory"
        self.shelf = Shelf(self.shelf_root, min_free_bytes=0, clock=lambda: self.clock.t)
        self.shelf.init_layout()
        self.watch = self.mac / "cos-export"
        self.watch.mkdir(mode=0o700, parents=True)
        self.config = self.mac / "memory_shelf.json"
        self.config.write_text(json.dumps({"schema_version": 1, "shelf_root": str(self.shelf_root), "inbox_root": str(self.mac / "inbox"),
                                           "headroom": {"min_free_bytes": 0}, "never_copy": [],
                                           "sources": {"minimoi-cos": {"kind": "cos", "root": str(self.watch), "never_copy": []}}}))
        self.jobs = self.mac / "data" / "jobs"
        (tmp / "ec2").mkdir(mode=0o755)
        self.vault = tmp / "ec2" / "vault"
        self.convs = {}

    def export(self, **kw):
        args = dict(dest=self.dest, vault_root=str(self.vault), recipient=self.recipient, history_root=str(self.history.root),
                    environment="prod", age_bin=self.age, owner_uid=os.geteuid(), require_root=False, now=self.clock)
        args.update(kw)
        return CX.run_export(**args)

    def pull(self, **kw):
        args = dict(dest=self.dest, identity_spec=self.identity, age_bin=self.age, expect_age=None, jobs_root=self.jobs, watch=self.watch,
                    state_dir=self.mac / ".vault-cos-pull", config_path=str(self.config), now=self.clock, alert_send=lambda t: "not_sent")
        args.update(kw)
        return CP.run_pull(**args)

    def sync(self):
        return S.run_sync(shelf_root=self.shelf_root, dest=self.dest, recipient=self.recipient, age_bin=self.age, expect_age=None,
                          jobs_root=self.jobs, state_dir=self.mac / ".vault-sync", now=self.clock, alert_send=lambda t: "not_sent")

    def apply(self):
        return A.run_apply(dest=self.dest, vault_root=str(self.vault), identity_spec=self.identity, index_runner="host", age_bin=self.age,
                           owner_uid=os.geteuid(), reader_uid=os.geteuid(), reader_gid=os.getegid(), require_root=False, now=self.clock)

    def approve(self):
        cfg = shelf_config.load(str(self.config)).sources["minimoi-cos"]
        if not approvals.dry_run_path(self.shelf, "minimoi-cos").is_file():
            watchers.run_source(self.shelf, cfg, now=self.clock.t)
        approvals.approve_source(self.shelf, "minimoi-cos", cfg.fingerprint(), OWNER)

    def seed(self):
        add_record(self.shelf, "grok", "g-seed", [("human", "seed question"), ("assistant", "seed answer")])
        index = self.mac / "data" / "memory-index" / "memory.sqlite"
        assert indexer.update(self.shelf_root, None, index_path=index, min_free_bytes=0, keyword_only=True)["code"] == "ok"
        P.run_publish(shelf_root=str(self.shelf_root), index_path=str(index), dest=self.dest, recipient=self.recipient,
                      workdir=str(self.mac / ".vault-publish-work"), age_bin=self.age, now=self.clock.t, enforce_window=False)
        P.run_refresh(dest=self.dest, vault_root=str(self.vault), identity_spec=self.identity, age_bin=self.age, owner_uid=os.geteuid(),
                      reader_uid=os.geteuid(), reader_gid=os.getegid(), require_root=False)
        assert self.apply()["inventory"] == "written"

    def carry(self):
        pulled = self.pull()
        self.clock.advance(minutes=5)
        self.sync()
        self.apply()
        self.clock.advance(minutes=5)
        synced = self.sync()
        self.clock.advance(minutes=5)
        return pulled, synced, self.export()

    def production_records(self) -> dict:
        shelf = (self.vault / "current" / "shelf").resolve()
        return {k: v for k, v in V.source_index(shelf).items() if k.startswith("minimoi-cos:")}

    def key(self, label) -> str:
        return f"minimoi-cos:prod:{self.convs[label]}"

    def everything_text(self) -> str:
        out = []
        for base in (self.watch, self.shelf_root, (self.vault / "current" / "shelf").resolve(), self.destdir, self.vault.parent):
            for p in base.rglob("*"):
                if p.is_file() and not p.name.endswith(".age") and not p.name.endswith(".sqlite"):
                    out.append(p.read_bytes().decode("utf-8", "replace"))
        return "\n".join(out)


@pytest.fixture
def t(tmp_path, monkeypatch):
    monkeypatch.setenv("MOI_RESERVE_GIB", "0")
    monkeypatch.delenv("MOI_BATCH_DEADLINE_EPOCH", raising=False)
    trip = Trip(tmp_path)
    trip.convs["kiln"] = trip.history.conversation(("KILN-SECRET schedule?", "KILN-SECRET fire Tuesday"))
    trip.convs["glaze"] = trip.history.conversation(("GLAZE-SECRET which?", "GLAZE-SECRET the blue one"))
    trip.history.conversation(("PRIVATE-SECRET words", "PRIVATE-SECRET reply"), private=True)
    trip.history.conversation(("NULLPRIV-SECRET words", "NULLPRIV-SECRET reply"), omit_private=True)
    trip.history.conversation(("DEV-SECRET words", "DEV-SECRET reply"), environment="dev")
    trip.seed()
    return trip


def test_production_cos_conversations_reach_the_production_vault_and_private_ones_never_travel(t):
    first = t.export()
    assert first["export"] == "ok" and first["sent"] == 2 and first["missing_in_vault"] == 2
    assert first["private"] == 2 and first["other_environment"] == 1
    pulled = t.pull()                                              # not approved yet: placed, waiting, a warning
    assert pulled["results"]["placed"] == "2" and pulled["results"]["capture"] == "not_approved" and pulled["state"] == "warn"
    assert t.production_records() == {}
    t.approve()
    t.clock.advance(minutes=10)
    pulled, synced, recon = t.carry()
    assert pulled["results"]["capture"] == "ok" and int(pulled["results"]["captured"]) == 2
    assert set(t.production_records()) == {t.key("kiln"), t.key("glaze")}
    assert synced["results"]["backlog_records"] == "0"
    assert (recon["in_vault_current"], recon["missing_in_vault"], recon["in_vault_stale"], recon["lag_oldest_s"]) == (2, 0, 0, 0)
    text = t.everything_text()
    assert not any(s in text for s in PRIVATE)                     # a private or other-environment conversation never travelled
    folder = t.production_records()[t.key("kiln")]["dir"]
    assert "KILN-SECRET" in ((t.vault / "current" / "shelf").resolve() / "sessions-raw" / folder / f"{folder}.md").read_text()
    # Retrievable in production's index, as a raw-tier record: a caller needs the raw tier (MINIMOI_VAULT_ASK_INCLUDE_RAW=1 and an
    # "all" grant) to find it; the curated tier does not see it until the owner designates it.
    current = (t.vault / "current").resolve()
    found = search.search("KILN-SECRET", shelf_root=current / "shelf", index_path=current / "index" / "memory.sqlite",
                          embedder=NoModel(), caller=policy.Caller(tier="all", scopes=("robert",), agent="cos"))
    assert found["keyword"] and not found["unresolved"]
    curated = search.search("KILN-SECRET", shelf_root=current / "shelf", index_path=current / "index" / "memory.sqlite",
                            embedder=NoModel(), caller=policy.Caller(tier="curated", scopes=("robert",), agent="cos"))
    assert not curated["keyword"]


def test_a_new_turn_in_production_adds_an_edition_in_the_production_vault(t):
    t.export(); t.approve(); t.carry()
    t.history.say(t.convs["kiln"], "KILN-SECRET and Thursday?", "KILN-SECRET Thursday works")
    t.clock.advance(hours=2)
    out = t.export()
    assert out["changed"] == 1 and out["sent"] == 1 and out["in_vault_stale"] == 1
    t.clock.advance(minutes=30)
    _, _, recon = t.carry()
    assert recon["in_vault_current"] == 2 and recon["in_vault_stale"] == 0
    shelf = (t.vault / "current" / "shelf").resolve()
    rid = t.production_records()[t.key("kiln")]["id"]
    assert V.record_versions(shelf)[0][rid].edition == 2


def test_a_pull_that_dies_before_its_ack_is_repulled_without_rewriting_and_acknowledged(t, monkeypatch):
    from core.memory_sync import mc_pull as MP
    t.approve()
    t.export()
    real = MP.MB.ack_doc
    monkeypatch.setattr(MP.MB, "ack_doc", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("killed before the ack")))
    died = t.pull()
    acks = t.destdir / "sync" / "cos-export-acks"
    assert died["state"] == "failed" and not (acks.exists() and list(acks.glob("*.json")))
    stamps = {p.name: p.stat().st_mtime_ns for p in t.watch.iterdir()}
    monkeypatch.setattr(MP.MB, "ack_doc", real)
    t.clock.advance(minutes=10)
    pulled, _, recon = t.carry()
    assert pulled["results"]["unchanged"] == "2" and {p.name: p.stat().st_mtime_ns for p in t.watch.iterdir()} == stamps
    assert recon["acked"] == 2 and recon["in_vault_current"] == 2


def test_a_placed_file_lost_before_capture_is_requeued_by_reconciliation_until_the_vault_holds_it(t):
    t.export()
    t.pull()                                                       # placed and acknowledged, not captured (not approved)
    (t.watch / f"prod--{t.convs['glaze']}.json").unlink()
    t.approve()
    _, _, recon = t.carry()
    assert recon["in_vault_current"] == 1 and recon["missing_in_vault"] == 1 and recon["gap_requeued"] == 0
    t.clock.advance(hours=13)
    requeue = t.export()
    assert requeue["gap_requeued"] == 1 and requeue["sent"] == 1
    _, _, recon = t.carry()
    assert recon["in_vault_current"] == 2 and recon["missing_in_vault"] == 0 and recon["lag_oldest_s"] == 0
