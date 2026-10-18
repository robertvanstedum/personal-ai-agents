"""Second adversarial review of the production MC capture path (17 Oct 2026): N1-N4 and the admission's raw OSError, as tests of the fixes.

Built on tests/vault/test_mc_e2e.py's Trip: every job is the real one (exporter in-process, vault-mc-export, vault-mc-pull with capture,
vault-sync, vault-apply). Each test failed before its fix."""
from __future__ import annotations

import io
import json
import os
import tarfile

import pytest

from core.memory_backup.backup import BackupError
from core.memory_sync import mc_batch as MB
from core.memory_sync import mc_export as MX
from core.memory_sync import mc_pull as MP
from tests.memory_shelf.test_mc_capture import X
from tests.vault.test_mc_e2e import t  # noqa: F401  (fixture)

H = 3600


def state(t):
    return json.loads((t.vault.parent / "vault-sync" / MX.STATE_NAME).read_text())


def dropping_runner(t, drop_ids):
    """The real exporter, with the named conversations no longer exported (deleted, or no longer the owner's kept on-record ones)."""
    def runner(argv, stdout):
        buf = io.BytesIO()
        tail = argv[argv.index(MX.EXPORTER) + 1:]
        code = X.main(tail + ["--guild-dir", str(t.portal.guild)], stream=buf)
        buf.seek(0)
        with tarfile.open(fileobj=buf, mode="r:") as src, tarfile.open(fileobj=stdout, mode="w|", format=tarfile.PAX_FORMAT) as out:
            for m in src:
                if any(i in m.name for i in drop_ids):
                    continue
                out.addfile(m, src.extractfile(m))
        return code
    return runner


def test_N1_a_pending_conversation_production_no_longer_exports_is_retired_not_warned_forever(t):
    first = t.export()
    bid = first["batch"]
    t.pull()
    (t.destdir / MB.ACK_PREFIX / f"{bid}.json").write_text("{not json")        # unreadable ack; the Mac never rewrites a pulled batch's
    kiln = t.convs["kiln"]["id"]
    t.clock.advance(hours=13)
    out = t.export(runner=dropping_runner(t, [kiln]))                           # kiln gone from production after it was sent
    assert out["gone_from_export"] == 1
    assert not any(n.endswith(f"{kiln}.json") for p in state(t)["pending"].values() for n in p["files"])
    for _ in range(3):
        t.pull()
        t.clock.advance(hours=13)
        out = t.export(runner=dropping_runner(t, [kiln]))
    assert out["unacked_past_grace"] == 0 and not any(n.endswith(f"{kiln}.json") for p in state(t)["pending"].values() for n in p["files"])


def test_N1_a_partial_export_keeps_what_it_did_not_export(t):
    t.export()
    kiln = t.convs["kiln"]["id"]

    def partial(argv, stdout):
        dropping_runner(t, [kiln])(argv, stdout)
        return 1                                                                # the exporter could not read everything this time
    t.clock.advance(hours=2)
    out = t.export(runner=partial)
    assert out["export"] == "partial" and out["gone_from_export"] == 0
    assert any(n.endswith(f"{kiln}.json") for p in state(t)["pending"].values() for n in p["files"])


def test_N2_a_healthy_mac_and_a_busy_conversation_is_not_reported_dead(t):
    t.approve()
    t.export()
    t.carry()
    t.clock.advance(minutes=30)
    _, _, out = t.carry()
    assert out["in_vault_current"] == 2 and out["lag_oldest_s"] == 0
    for i in range(8):                                                          # one new turn per 2 h cycle, every cycle delivered
        t.portal.say(t.convs["kiln"], ("owner", f"more {i}"))
        t.clock.advance(hours=2)
        _, _, out = t.carry()
    assert out["behind_past_grace"] == 0 and out["lag_oldest_s"] < 12 * H
    assert out["unacked_past_grace"] == 0 and out["oldest_unacked_s"] < 12 * H


def test_N2_a_dead_mac_still_shows_while_the_conversation_keeps_changing(t):
    t.approve()
    t.export()
    t.carry()
    for i in range(8):                                                          # the Mac stopped: no pull, no sync from here on
        t.portal.say(t.convs["kiln"], ("owner", f"unheard {i}"))
        t.clock.advance(hours=2)
        out = t.export()
    assert out["behind_past_grace"] >= 1 and out["lag_oldest_s"] > 12 * H
    assert out["unacked_past_grace"] >= 1 and out["oldest_unacked_s"] > 12 * H


def test_N3_an_unexpected_current_link_is_an_unknown_vault_and_sending_goes_on(t):
    t.export()
    t.pull()
    cur = t.vault / "current"
    target = os.readlink(cur)
    cur.unlink()
    os.symlink("elsewhere/" + os.path.basename(target), cur)
    t.converse("new", ("owner", "NEW words"), ("mc", "NEW reply"))
    t.clock.advance(hours=1)
    out = t.export()
    assert out["vault"] == "unreadable" and out["sent"] >= 1 and out["missing_in_vault"] is None


def test_N3_a_release_without_its_source_index_is_unknown_never_all_missing(t):
    t.export()
    index = (t.vault / "current" / "shelf" / "_ledger" / "source_index.json").resolve()
    os.chmod(index.parent, 0o700)
    index.unlink()                                                              # a damaged or half-pruned release
    t.clock.advance(hours=13)
    out = t.export()
    assert out["vault"] == "unreadable" and out["gap_requeued"] == 0 and out["missing_in_vault"] is None


def test_N4_the_temp_sweep_and_export_names_match_exactly_never_with_a_trailing_newline(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    good = ".production--c-0123456789ab.json.tmp-12-deadbeef"
    newline = ".production--c-0123456789ab.json.tmp-3-0000cccc\n"
    (w / good).write_text("plain")
    (w / newline).write_text("not ours")
    MP.sweep_temps(w)
    assert sorted(os.listdir(w)) == [newline]
    assert not MB.name_ok("production--c-0123456789ab.json\n") and MB.name_ok("production--c-0123456789ab.json")


def test_an_unopenable_lock_is_a_fixed_code_not_a_traceback(t):
    sync = t.vault.parent / "vault-sync"
    sync.mkdir(mode=0o700, exist_ok=True)
    os.symlink("/nonexistent/elsewhere", sync / "mc-export.lock")               # O_NOFOLLOW refuses a linked lock file
    with pytest.raises(BackupError) as exc:
        with MX.Admission(str(t.vault), owner_uid=os.geteuid()):
            pass
    assert exc.value.code == "lock_not_openable"
