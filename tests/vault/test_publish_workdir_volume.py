"""The seed's plaintext scratch may live on the owner-approved external volume only when that exact volume is mounted
(17 Oct 2026; the same rule as the approved backup wrapper). Synthetic volumes; diskutil is injected."""
import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("publish_production_guard", ROOT / "scripts/vault/publish_production.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

UUID = "C28F5ADD-B398-4408-AECD-EDB4FC091A79"


@pytest.fixture
def volumes(tmp_path, monkeypatch):
    vols = tmp_path / "Volumes"
    (vols / "Files and Photos" / "MiniMoi").mkdir(parents=True)
    # A real external volume has its own device; a tmp dir shares the root's, so pretend the root differs.
    real_stat = Path.stat
    def stat(self, *a, **kw):
        st = real_stat(self, *a, **kw)
        if self == Path("/"):
            return os.stat_result((st.st_mode, st.st_ino, st.st_dev + 1, *st[3:]))
        return st
    monkeypatch.setattr(Path, "stat", stat)
    # ... and that the home folder's disk is not this synthetic volume's (they share a device in a test).
    monkeypatch.setattr(P, "_internal_devices", lambda: {real_stat(Path("/")).st_dev + 1})
    return vols


def info(uuid=UUID, writable=True, mount=None):
    return lambda volume: {"VolumeUUID": uuid, "MountPoint": mount or str(volume), "WritableVolume": writable}


def work(vols):
    return str(vols / "Files and Photos" / "MiniMoi" / ".vault-publish-work")


def test_the_exact_mounted_writable_volume_is_accepted_and_nothing_is_created(volumes):
    assert P.check_workdir_volume(work(volumes), UUID, volumes=volumes, info=info()) is None
    assert not Path(work(volumes)).exists()


@pytest.mark.parametrize("uuid,kw,code", [
    ("", {}, "workdir_volume_uuid_required"),
    (UUID, {"uuid": "OTHER-UUID"}, "workdir_volume_unavailable"),
    (UUID, {"writable": False}, "workdir_volume_unavailable"),
    (UUID, {"mount": "/elsewhere"}, "workdir_volume_unavailable"),
])
def test_a_wrong_missing_or_readonly_volume_refuses(volumes, uuid, kw, code):
    assert P.check_workdir_volume(work(volumes), uuid, volumes=volumes, info=info(**kw)) == code


def test_an_unmounted_volume_refuses_before_anything_is_created(tmp_path):
    vols = tmp_path / "Volumes"
    vols.mkdir()
    workdir = vols / "Files and Photos" / "MiniMoi" / ".vault-publish-work"
    assert P.check_workdir_volume(str(workdir), UUID, volumes=vols, info=info()) == "workdir_volume_unavailable"
    assert not (vols / "Files and Photos").exists()


def test_a_symlink_on_the_path_refuses(volumes, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    link = volumes / "Files and Photos" / "MiniMoi" / ".vault-publish-work"
    link.symlink_to(tmp_path / "elsewhere")
    assert P.check_workdir_volume(str(link), UUID, volumes=volumes, info=info()) == "workdir_unsafe"


def test_an_internal_workdir_needs_no_uuid_and_a_stray_uuid_is_refused(tmp_path):
    assert P.check_workdir_volume(str(tmp_path / "w"), "", volumes=tmp_path / "Volumes") is None
    assert P.check_workdir_volume(str(tmp_path / "w"), UUID, volumes=tmp_path / "Volumes") == "workdir_volume_uuid_but_not_external"


def test_run_refuses_an_unguarded_external_workdir_before_touching_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(P, "VOLUMES", tmp_path / "Volumes")
    workdir = tmp_path / "Volumes" / "Files and Photos" / "MiniMoi" / ".vault-publish-work"
    code = P.main(["--workdir", str(workdir), "--recipient", "age1" + "q" * 58, "--jobs-root", str(tmp_path / "jobs"),
                   "run", "--execute", "--allow-outside-window"])
    assert code == P.EXIT_REFUSED and "workdir_volume_uuid_required" in capsys.readouterr().err
    assert not (tmp_path / "Volumes").exists() and not (tmp_path / "jobs").exists()


# ── r2 (review, 18 Oct): decided on what the path really is ──────────────────

def test_a_dotdot_path_is_refused_even_with_the_right_uuid(volumes):
    sneaky = str(volumes / "Files and Photos" / ".." / "Backups" / "w")
    assert P.check_workdir_volume(sneaky, UUID, volumes=volumes, info=info()) == "workdir_unsafe"


@pytest.mark.parametrize("spell", [
    lambda v, w: w.replace(str(v), str(v.parent / "volumes")),        # APFS ignores case
    lambda v, w: "/" + w,                                              # a repeated leading slash
])
def test_another_spelling_of_volumes_still_needs_the_uuid(volumes, spell):
    assert P.check_workdir_volume(spell(volumes, work(volumes)), "", volumes=volumes, info=info()) == "workdir_volume_uuid_required"


def test_a_symlinked_ancestor_into_a_volume_is_refused(volumes, tmp_path):
    (tmp_path / "link").symlink_to(volumes / "Files and Photos")
    assert P.check_workdir_volume(str(tmp_path / "link" / "w"), "", volumes=volumes, info=info()) == "workdir_unsafe"


def test_a_path_outside_volumes_on_another_device_is_not_internal(tmp_path):
    other = {0}                                                        # the tmp folder's device is not one of the Mac's own
    assert P.check_workdir_volume(str(tmp_path / "w"), "", volumes=tmp_path / "Volumes", internal_devs=other) == "workdir_volume_uuid_required"
    assert P.check_workdir_volume(str(tmp_path / "w"), UUID, volumes=tmp_path / "Volumes", internal_devs=other) == "workdir_volume_unavailable"


def test_a_volume_on_the_macs_own_disk_is_refused(volumes):
    own = {volumes.stat().st_dev}                                      # e.g. /Volumes/Macintosh HD: the boot disk, not external
    assert P.check_workdir_volume(work(volumes), UUID, volumes=volumes, info=info(), internal_devs=own) == "workdir_volume_unavailable"
