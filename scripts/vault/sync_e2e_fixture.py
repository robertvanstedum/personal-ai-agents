#!/usr/bin/env python3
"""sync_e2e_fixture.py: the owner's deployment condition for the Vault incremental sync, run end to end on fixtures (17 Oct 2026).

Both real jobs, nothing mocked between them: the Mac's ``vault-sync`` (core/memory_sync/sync.py) writes encrypted batches to a
``file://`` destination standing in for S3, and EC2's ``vault-apply`` (core/memory_sync/apply.py) applies them to a fixture production
vault that was seeded by r3's own snapshot transport (``production.run_publish`` + ``run_refresh``). The clock is injected, so daytime,
night and a sleep are exact. Age is the pinned real binary with a throwaway identity made in the work folder. Synthetic records only.

Scenarios (each prints its checks; any failed check makes the exit code 1):

  seed          r3 publish + refresh install the snapshot; the first vault-apply writes production's first inventory
  normal        a new record, a new edition and a metadata-only change reach production; status ends at backlog 0
  interrupted_upload   the upload dies before COMPLETE: production never sees it, the versions stay pending, the next run re-sends
  interrupted_apply    vault-apply dies mid-placement and again just before the switch: no partial record serves, no ack; the next
                       apply completes; then a crash after the switch: the re-run acknowledges without building another release
  retry_idempotent     a retry re-sends a version production already holds: acknowledged ``stored``, no second release
  rejected_retry       a batch whose ciphertext was corrupted in transit is rejected; the Mac keeps it pending with backoff and the retry lands
  gap_recovery         a version is deleted from the production shelf; reconciliation re-queues it; it is re-applied; identical_set
  sleep_catch_up       a daytime backlog bigger than the light bound sends one light batch (deferred_heavy); after an overnight sleep one
                       run on wake does the same bound; the night run sends the rest; backlog 0
  status               last sync, backlog and oldest pending are checked after every step above

    sync_e2e_fixture.py [--workdir DIR] [--age-bin PATH] [--age-keygen PATH] [--json]

Writes nothing outside --workdir (default: a new temp folder, removed afterwards unless --keep).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.jobs import status as job_status                                   # noqa: E402
from core.memory_backup import backup as B                                   # noqa: E402
from core.memory_backup import production as P                               # noqa: E402
from core.memory_index import indexer, search, store                         # noqa: E402
from core.memory_index import policy                                         # noqa: E402
from core.memory_shelf import bundle as bundles                              # noqa: E402
from core.memory_shelf import events as ev                                   # noqa: E402
from core.memory_shelf import record                                         # noqa: E402
from core.memory_shelf.sessions import Parsed                                # noqa: E402
from core.memory_shelf.shelf import Shelf                                    # noqa: E402
from core.memory_sync import apply as A                                      # noqa: E402
from core.memory_sync import sync as S                                       # noqa: E402
from core.memory_sync import versions as V                                   # noqa: E402

REAL_AGE = Path.home() / "minimoi-staging" / "tools" / "age-v1.3.2" / "age"
DAY = datetime(2026, 10, 19, 19, 0, tzinfo=timezone.utc)                      # 14:00 Chicago: outside the batch window
LIGHT, HEAVY = 24 << 10, 64 << 10                                            # fixture-sized bounds (bytes of record files)


class Crash(RuntimeError):
    """An injected kill."""


class _NoEmbedder:
    """Production has none; keyword search never asks it for anything but release()."""

    def release(self):
        pass


class Clock:
    def __init__(self, t: datetime):
        self.t = t

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw) -> None:
        self.t += timedelta(**kw)


class World:
    def __init__(self, root: Path, age_bin: str, recipient: str, identity_spec: str):
        self.root = root
        self.clock = Clock(DAY)
        self.shelf = Shelf(root / "mac" / "data" / "memory", min_free_bytes=0, clock=lambda: self.clock.t)
        self.shelf.init_layout()
        self.index = root / "mac" / "data" / "memory-index" / "memory.sqlite"
        self.jobs = root / "mac" / "data" / "jobs"
        self.state = root / "mac" / ".vault-sync"
        self.destdir = root / "s3"
        self.dest = f"file://{self.destdir}"
        self.vault = root / "ec2" / "vault"
        self.vault.parent.mkdir(mode=0o755, parents=True)
        self.recipient, self.identity, self.age = recipient, identity_spec, age_bin
        self.ids: dict[str, str] = {}
        self.checks: list[dict] = []
        self.scenario = ""

    # ── the Mac's shelf ──
    def add(self, name: str, provider: str, sid: str, turns: list, created: str = "2026-10-01T10:00:00Z") -> str:
        chair = {"grok": "Grok", "codex": "Codex", "claude-code": "Claude Code", "claude-ai": "Claude"}[provider]
        parsed = Parsed(provider, chair, source_id=sid, identity="source", normalizer=1)
        for i, (speaker, text) in enumerate(turns, 1):
            parsed.add(speaker, text, i, "test")
        raw = repr(turns).encode()
        b = bundles.from_parsed(parsed, hashlib.sha256(raw).hexdigest(), len(raw), key=f"{provider}:{sid}", title=f"{provider} {sid}",
                                origin=f"fixture:{provider}", created=created, kind="session", ledger_key=bundles.ledger_key_for(provider, sid))
        result = self.shelf.ingest(b)
        assert result.outcome in ("captured", "edition-added"), result
        self.ids[name] = result.record_id
        return result.record_id

    def designate(self, name: str) -> None:
        path = self.shelf.find_record(self.ids[name])
        record.append_event(path, ev.make_event("designated-curated", "robert", now=self.clock.t, via="moi-designate"))

    # ── the two jobs ──
    def sync(self, **kw) -> dict:
        self.clock.advance(minutes=1)                                       # every run has its own finish time
        args = dict(shelf_root=str(self.shelf.root), dest=self.dest, recipient=self.recipient, age_bin=self.age, expect_age="v1.3.2",
                    jobs_root=str(self.jobs), state_dir=str(self.state), light_bytes=LIGHT, light_records=200, heavy_bytes=HEAVY,
                    now=self.clock, alert_send=lambda text: "not_sent")
        args.update(kw)
        return S.run_sync(**args)

    def apply(self, **kw) -> dict:
        args = dict(dest=self.dest, vault_root=str(self.vault), identity_spec=self.identity, index_runner="host", age_bin=self.age,
                    expect_age="v1.3.2", owner_uid=os.geteuid(), reader_uid=os.geteuid(), reader_gid=os.getegid(), require_root=False,
                    now=self.clock)
        args.update(kw)
        return A.run_apply(**args)

    # ── what production holds ──
    def serving(self) -> Path:
        return (self.vault / "current").resolve()

    def releases(self) -> list:
        return sorted(p.name for p in (self.vault / "releases").iterdir())

    def acks(self) -> list:
        base = self.destdir / V.ACK_PREFIX
        return sorted(p.stem for p in base.glob("*.json") if p.name != V.INVENTORY_NAME) if base.is_dir() else []

    def batches(self, complete_only: bool = True) -> list:
        base = self.destdir / V.BATCH_PREFIX
        if not base.is_dir():
            return []
        return sorted(p.name for p in base.iterdir() if not complete_only or (p / B.COMPLETE).exists())

    def identical(self) -> bool:
        mac, _ = V.record_versions(self.shelf.root)
        prod, _ = V.record_versions(self.serving() / "shelf")
        inv = json.loads((self.destdir / V.ACK_PREFIX / V.INVENTORY_NAME).read_text())
        return V.version_set_digest(V.entries_of(mac)) == V.version_set_digest(V.entries_of(prod)) == inv["version_set_digest"]

    def reconcile(self) -> dict:
        import importlib.util
        spec = importlib.util.spec_from_file_location("vault_reconcile", Path(__file__).with_name("reconcile.py"))
        R = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(R)
        return R.reconcile(self.shelf.root, [], None, None, None, "2026", inventory=self.destdir / V.ACK_PREFIX / V.INVENTORY_NAME,
                           sync_state=self.state, sync_status=self.jobs / "vault-sync.json", now=self.clock.t)

    def status(self) -> dict:
        return job_status.read_status(self.jobs, "vault-sync.json", "vault-sync") or {}

    def keyword(self, query: str) -> list:
        old = (store.sqlite_vec, search.sqlite_vec)
        store.sqlite_vec = search.sqlite_vec = None                         # read the way production's vault-ask does
        try:
            r = search.search(query, shelf_root=self.vault / "current" / "shelf", embedder=_NoEmbedder(),
                              index_path=self.vault / "current" / "index" / P.INDEX_NAME, caller=policy.Caller(tier="all"), k=5)
        finally:
            store.sqlite_vec, search.sqlite_vec = old
        return [h["record_id"] for h in r["keyword"]]

    def check(self, name: str, ok: bool, **facts) -> None:
        self.checks.append({"scenario": self.scenario, "check": name, "ok": bool(ok), **facts})

    def check_status(self, *, backlog: int, success_now: bool) -> None:
        st = self.status()
        r = st.get("results", {})
        self.check("status_backlog", r.get("backlog_records") == str(backlog), got=r.get("backlog_records"), want=str(backlog))
        if success_now:
            self.check("status_last_sync_is_this_run", st.get("last_success_at") == st.get("finished_at"))
        else:
            self.check("status_last_sync_not_advanced", st.get("last_success_at") is not None
                       and st.get("last_success_at") != st.get("finished_at"))
        oldest = int(r.get("oldest_pending_age_s", "-1"))
        since = [job_status.parse_time(t) for t in json.loads((self.state / "pending.json").read_text()).get("backlog_since", {}).values()]
        finished = job_status.parse_time(st.get("finished_at"))
        want = int((finished - min(since)).total_seconds()) if since and finished else 0
        self.check("status_oldest_pending", oldest == want and (backlog == 0) == (not since), oldest_pending_age_s=oldest, want=want)


def converge(w: World, rounds: int = 3) -> None:
    """Sync, apply, sync again (the ack clears pending), as the schedules would."""
    for _ in range(rounds):
        w.sync()
        w.apply()
        w.clock.advance(minutes=15)
    w.sync()


# ── scenarios ──
def seed(w: World) -> None:
    w.scenario = "seed"
    w.add("bread", "grok", "g-bread", [("human", "How do I make sourdough rise?"), ("assistant", "Feed the starter twice a day.")],
          created="2026-05-03T10:00:00Z")
    w.add("sky", "grok", "g-sky", [("human", "Which telescope for planets?"), ("assistant", "A 6 inch Dobsonian shows Jupiter.")])
    w.add("code", "codex", "c-1", [("human", "Fix the gateway test"), ("assistant", "The gateway restarted during the test.")])
    assert indexer.update(w.shelf.root, None, index_path=w.index, min_free_bytes=0, keyword_only=True)["code"] == "ok"
    sync_before = w.sync()
    w.check("no_inventory_refuses", sync_before["results"].get("sync") == "no_production_inventory" and not w.batches(False))
    m = P.run_publish(shelf_root=str(w.shelf.root), index_path=str(w.index), dest=w.dest, recipient=w.recipient,
                      workdir=str(w.root / "mac" / ".vault-publish-work"), age_bin=w.age, now=w.clock.t, enforce_window=False)
    P.run_refresh(dest=w.dest, vault_root=str(w.vault), identity_spec=w.identity, age_bin=w.age, owner_uid=os.geteuid(),
                  reader_uid=os.geteuid(), reader_gid=os.getegid(), require_root=False)
    w.check("snapshot_installed", (w.vault / "current").is_symlink() and w.serving().name == m["attempt"])
    out = w.apply()
    w.check("first_inventory_written", out["inventory"] == "written" and (w.destdir / V.ACK_PREFIX / V.INVENTORY_NAME).is_file())
    w.check("seed_identical", w.identical())
    s = w.sync()
    w.check("synced_after_seed", s["results"].get("sync") == "ok" and s["results"].get("sent") == "0")
    w.check_status(backlog=0, success_now=True)


def normal(w: World) -> None:
    w.scenario = "normal"
    w.add("garden", "grok", "g-garden", [("human", "When to plant tomatoes?"), ("assistant", "After the last frost, seedlings first.")])
    w.add("bread", "grok", "g-bread", [("human", "How do I make sourdough rise?"), ("assistant", "Use a banneton and a dutch oven.")])
    w.designate("sky")
    s = w.sync()
    w.check("three_versions_sent", s["results"].get("sent") == "3", sent=s["results"].get("sent"))
    w.check_status(backlog=3, success_now=False)
    before = w.serving().name
    out = w.apply()
    w.check("applied_new_release", out["applied"] == 3 and w.serving().name != before)
    w.check("keyword_finds_new_text", w.ids["garden"] in w.keyword("tomatoes") and w.ids["bread"] in w.keyword("banneton"))
    w.check("old_edition_text_gone", w.ids["bread"] not in w.keyword("starter"))
    w.clock.advance(minutes=15)
    s = w.sync()
    w.check("acked_and_clear", s["results"].get("acked") == "3" and s["results"].get("backlog_records") == "0")
    w.check_status(backlog=0, success_now=True)
    w.check("identical", w.identical())


def interrupted_upload(w: World) -> None:
    w.scenario = "interrupted_upload"
    w.add("travel", "grok", "g-travel", [("human", "Train from Vienna to Prague?"), ("assistant", "About four hours, direct.")])

    def dying_put(local, key, replace=False):
        if key.endswith(B.COMPLETE):
            raise Crash("killed before COMPLETE")
        return B.Dest(w.dest).put(local, key, replace=replace)
    s = w.sync(put=dying_put)
    orphans = [b for b in w.batches(False) if b not in w.batches(True)]
    w.check("orphan_without_complete", len(orphans) == 1 and s["state"] == "failed")
    pending = json.loads((w.state / "pending.json").read_text())["pending"]
    w.check("still_pending_sending", pending.get(w.ids["travel"], {}).get("state") == "sending")
    out = w.apply()
    w.check("production_ignores_orphan", out["batches"] == 0 and orphans[0] not in w.acks())
    w.clock.advance(minutes=5)
    s = w.sync()
    w.check("resent_in_new_batch", s["results"].get("sent") == "1" and s["results"].get("retried") == "1")
    w.apply()
    w.clock.advance(minutes=15)
    s = w.sync()
    w.check("cleared_after_ack", s["results"].get("backlog_records") == "0")
    folders = [p for p in (w.serving() / "shelf" / "sessions-raw").iterdir() if p.name.endswith(w.ids["travel"][-8:].lower())]
    w.check("no_duplicate_record", len(folders) == 1)
    w.check("identical", w.identical())
    w.check_status(backlog=0, success_now=True)


def interrupted_apply(w: World) -> None:
    w.scenario = "interrupted_apply"
    w.add("music", "claude-ai", "a-music", [("human", "Learn the cello at 50?"), ("assistant", "Yes, with a teacher and daily practice.")])
    w.add("sky", "grok", "g-sky", [("human", "Which telescope for planets?"), ("assistant", "A 6 inch Dobsonian; add a Barlow lens.")])
    w.sync()
    serving, acks = w.serving().name, w.acks()

    for where in ("placed", "before_switch"):
        def boom(*_a, where=where):
            raise Crash(where)
        try:
            w.apply(hooks={where: boom})
            crashed = False
        except Crash:
            crashed = True
        w.check(f"crash_{where}_leaves_current", crashed and w.serving().name == serving and w.acks() == acks)
        w.check(f"crash_{where}_no_partial_record", w.ids["music"] not in {v for v in V.record_versions(w.serving() / "shelf")[0]})
    s = w.sync()
    w.check("mac_still_pending_after_crashes", s["results"].get("backlog_records") == "2" and s["results"].get("acked") == "0")
    w.check_status(backlog=2, success_now=False)
    out = w.apply()
    w.check("next_apply_completes", out["applied"] == 2 and w.serving().name != serving)
    w.clock.advance(minutes=15)
    w.sync()
    w.check_status(backlog=0, success_now=True)

    w.add("music", "claude-ai", "a-music", [("human", "Learn the cello at 50?"), ("assistant", "Yes; start with open strings.")])
    w.sync()
    try:
        w.apply(hooks={"after_switch": lambda *_a: (_ for _ in ()).throw(Crash("after switch"))})
    except Crash:
        pass
    releases = w.releases()
    out = w.apply()
    w.check("after_switch_crash_reacked_without_rebuild", out["stored"] == 1 and not out["built"] and w.releases() == releases)
    w.clock.advance(minutes=15)
    w.sync()
    w.check("identical", w.identical())
    w.check_status(backlog=0, success_now=True)


def retry_idempotent(w: World) -> None:
    w.scenario = "retry_idempotent"
    w.add("chess", "grok", "g-chess", [("human", "Best chess opening for beginners?"), ("assistant", "The Italian Game.")])
    w.sync()
    w.clock.advance(hours=1, minutes=1)                                       # no ack yet: the retry time passes
    s = w.sync()
    w.check("retried_same_version", s["results"].get("retried") == "1" and len(w.batches()) >= 2)
    out = w.apply()
    w.check("two_batches_one_placement", out["batches"] == 2 and out["stored"] == 2 and out["applied"] == 1 and out["built"], out=_small(out))
    again = w.apply()
    w.check("nothing_left_to_build", again["code"] == "noop" and not again["built"], out=_small(again))
    w.clock.advance(minutes=15)
    s = w.sync()
    w.check("cleared", s["results"].get("backlog_records") == "0")
    w.check("identical", w.identical())


def rejected_retry(w: World) -> None:
    w.scenario = "rejected_retry"
    w.add("bike", "claude-ai", "a-bike", [("human", "Road or gravel bike?"), ("assistant", "Gravel, for the mixed roads you ride.")])
    w.sync()
    newest = w.batches()[-1]
    cipher = w.destdir / V.BATCH_PREFIX / newest / V.BATCH_OBJECT
    data = bytearray(cipher.read_bytes())
    data[len(data) // 2] ^= 0xFF                                               # corrupted in transit
    os.chmod(cipher, 0o600)
    cipher.write_bytes(bytes(data))
    out = w.apply()
    w.check("batch_rejected", out["rejected_batches"] == 1 and out["rejected"] == 1)
    w.clock.advance(minutes=15)
    s = w.sync()
    entry = json.loads((w.state / "pending.json").read_text())["pending"].get(w.ids["bike"], {})
    w.check("kept_pending_with_reason", entry.get("state") == "rejected" and entry.get("reason") == "ciphertext_mismatch" and s["state"] == "warn")
    w.check_status(backlog=1, success_now=False)
    w.clock.advance(minutes=30)
    s = w.sync()
    w.check("not_resent_before_backoff", s["results"].get("sent") == "0")
    w.clock.advance(hours=1)
    s = w.sync()
    w.check("resent_after_backoff", s["results"].get("sent") == "1" and s["results"].get("retried") == "1")
    w.apply()
    w.clock.advance(minutes=15)
    w.sync()
    w.check_status(backlog=0, success_now=True)
    w.check("identical", w.identical())


def gap_recovery(w: World) -> None:
    w.scenario = "gap_recovery"
    lost = V.record_versions(w.serving() / "shelf")[0][w.ids["garden"]]
    shutil.rmtree(w.serving() / "shelf" / lost.kind / lost.name)                # production loses a version it had acknowledged
    w.clock.advance(hours=21)                                                   # the daily inventory notices
    out = w.apply()
    w.check("inventory_rewritten", out["inventory"] == "written")
    r = w.reconcile()
    w.check("reconcile_reports_gap", r["production"]["versions"]["gaps"] == 1 and r["production"]["versions"]["identical_set"] is False)
    s = w.sync()
    w.check("gap_requeued", s["results"].get("gap_requeued") == "1" and s["results"].get("sent") == "1")
    w.apply()
    w.clock.advance(minutes=15)
    w.sync()
    r = w.reconcile()
    w.check("identical_set_true", r["production"]["versions"]["identical_set"] is True and r["production"]["_all"]["identical_set"] is True,
            versions=r["production"]["versions"]["identical_set"])
    w.check("reconcile_last_sync", r["sync"]["last_sync_at"] == w.status().get("last_success_at") and r["sync"]["backlog_records"] == 0)
    w.check_status(backlog=0, success_now=True)


def sleep_catch_up(w: World) -> None:
    w.scenario = "sleep_catch_up"
    w.clock.t = w.clock.t.replace(hour=19, minute=0) + timedelta(days=1)       # next day, 14:00 Chicago
    long = "planting notes " * 400                                              # about 6 KiB of text per record (12 KiB with its edition)
    for i in range(5):
        w.add(f"big{i}", "codex", f"c-big-{i}", [("human", f"notes {i}"), ("assistant", long + str(i))])
    s = w.sync()
    sent, deferred = int(s["results"].get("sent", "0")), int(s["results"].get("deferred_heavy", "0"))
    w.check("daytime_light_batch", s["results"].get("mode") == "light" and 0 < sent < 5 and deferred == 5 - sent, sent=sent, deferred=deferred)
    newest = json.loads((w.destdir / V.BATCH_PREFIX / w.batches()[-1] / B.MANIFEST).read_text())
    w.check("light_bound_respected", newest["record_bytes"] <= LIGHT, record_bytes=newest["record_bytes"])
    w.apply()
    w.clock.t = (w.clock.t + timedelta(days=1)).replace(hour=15, minute=30)    # asleep until 10:30 Chicago next morning: launchd runs
                                                                                # the missed interval ONCE on wake, in daytime
    s = w.sync()
    w.check("wake_run_same_bound", s["results"].get("mode") == "light" and int(s["results"].get("deferred_heavy", "0")) > 0
            and int(s["results"].get("acked", "0")) == sent)
    w.check_status(backlog=5 - sent, success_now=False)
    w.apply()
    w.clock.t = (w.clock.t + timedelta(days=1)).replace(hour=5, minute=30)      # 00:30 Chicago: the window
    s = w.sync()
    w.check("night_sends_the_rest", s["results"].get("mode") == "heavy" and s["results"].get("deferred_heavy") == "0")
    w.apply()
    w.clock.advance(minutes=15)
    w.sync()
    w.check_status(backlog=0, success_now=True)
    w.check("identical", w.identical())


def _small(out: dict) -> dict:
    return {k: v for k, v in out.items() if k in ("code", "batches", "stored", "superseded", "rejected", "applied", "built", "inventory")}


SCENARIOS = (seed, normal, interrupted_upload, interrupted_apply, retry_idempotent, rejected_retry, gap_recovery, sleep_catch_up)


def make_identity(workdir: Path, keygen: str) -> tuple[str, str]:
    """A throwaway age identity in the work folder: (public recipient, file: identity spec)."""
    keys = workdir / "keys"
    keys.mkdir(mode=0o700, parents=True)
    ident = keys / "identity.agekey"
    subprocess.run([keygen, "-o", str(ident)], check=True, capture_output=True)
    recipient = subprocess.run([keygen, "-y", str(ident)], check=True, capture_output=True, text=True).stdout.strip()
    return recipient, f"file:{ident}"


def run(workdir: Path, age_bin: str, recipient: str, identity_spec: str) -> dict:
    old_env = {k: os.environ.get(k) for k in ("MOI_RESERVE_GIB", "MOI_BATCH_DEADLINE_EPOCH")}
    os.environ["MOI_RESERVE_GIB"] = "0"                                         # fixture disk; the reserve itself has its own tests
    os.environ.pop("MOI_BATCH_DEADLINE_EPOCH", None)
    try:
        w = World(workdir, age_bin, recipient, identity_spec)
        completed = set()
        for scenario in SCENARIOS:
            try:
                scenario(w)
                completed.add(scenario.__name__)
            except Exception as exc:                                            # noqa: BLE001 - recorded as a failed check
                w.check("scenario_completed", False, error=f"{type(exc).__name__}: {exc}"[:300])
                break
        failed = [c for c in w.checks if not c["ok"]]
        by_scenario = {s.__name__: s.__name__ in completed and all(c["ok"] for c in w.checks if c["scenario"] == s.__name__) for s in SCENARIOS}
        return {"ok": not failed and all(by_scenario.values()), "checks": len(w.checks), "failed": failed, "by_scenario": by_scenario,
                "detail": w.checks}
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--age-bin", default=str(REAL_AGE))
    ap.add_argument("--age-keygen", default=None)
    ap.add_argument("--json", action="store_true", help="print the full check list")
    a = ap.parse_args(argv)
    keygen = a.age_keygen or str(Path(a.age_bin).with_name("age-keygen"))
    if not (Path(a.age_bin).is_file() and Path(keygen).is_file()):
        print("refused: age_unavailable", file=sys.stderr)
        return 2
    work = Path(a.workdir) if a.workdir else Path(tempfile.mkdtemp(prefix="vault-sync-e2e-"))
    work.mkdir(parents=True, exist_ok=True)
    try:
        recipient, identity = make_identity(work, keygen)
        report = run(work, a.age_bin, recipient, identity)
    finally:
        if not a.workdir and not a.keep:
            shutil.rmtree(work, ignore_errors=True)
    out = report if a.json else {k: v for k, v in report.items() if k != "detail"}
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
