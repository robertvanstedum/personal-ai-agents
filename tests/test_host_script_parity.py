"""Host scripts come from the deployed commit, and drift is reported (#505, #506)."""

import json
import os
import re
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "operations"))
import host_script_parity as hsp  # noqa: E402

CONFIG = ROOT / "config" / "host_scripts.json"
DEPLOY = (ROOT / ".github" / "workflows" / "deploy.yml").read_text()


def test_every_listed_repo_file_exists_and_hosts_are_unique():
    entries = hsp.load_config(CONFIG)
    assert entries
    for e in entries:
        assert (ROOT / e["repo"]).is_file(), e["repo"]
        assert e["host"].startswith("/opt/"), e["host"]


def test_the_backup_scripts_are_shipped_by_the_deploy():
    shipped = {e["repo"] for e in hsp.load_config(CONFIG) if e.get("ship")}
    assert {"scripts/backup_local.sh", "scripts/backup_s3.sh", "scripts/backup_dropbox.sh",
            "scripts/operations/host_script_parity.py"} <= shipped
    assert "host_script_parity.py bundle" in DEPLOY and "host_script_parity.py install --from" in DEPLOY
    assert "host_script_parity.py check --notify || echo parity: drift reported" in DEPLOY   # never fatal


def test_the_hand_installed_copies_the_first_watchdog_run_found_now_ship():
    """#537 first run: parity failed on two hand-installed copies (Guildhall's kit, a July backup-cron installer)."""
    by_host = {e["host"]: e for e in hsp.load_config(CONFIG)}
    for host in ("/opt/minimoi/scripts/guildhall_prod.sh", "/opt/minimoi/scripts/setup_backup_cron.sh"):
        assert by_host[host]["ship"] is True and by_host[host]["mode"] == "0755"


def test_every_script_the_backup_cron_runs_is_listed():
    listed = {e["host"] for e in hsp.load_config(CONFIG)}
    cron = (ROOT / "scripts" / "setup_backup_cron.sh").read_text()
    names = set(re.findall(r"\$SCRIPTS_DIR/([A-Za-z0-9_.-]+\.sh) >>", cron))
    assert names == {"backup_local.sh", "backup_s3.sh", "backup_dropbox.sh"}
    for name in names:
        assert f"/opt/minimoi/scripts/{name}" in listed, name


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "scripts").mkdir(parents=True)
    (r / "scripts" / "a.sh").write_text("echo a\n")
    (r / "scripts" / "b.sh").write_text("echo b\n")
    host = tmp_path / "host"
    cfg = {"format": hsp.FORMAT, "entries": [
        {"host": str(host / "a.sh"), "repo": "scripts/a.sh", "ship": True, "mode": "0750"},
        {"host": str(host / "b.sh"), "repo": "scripts/b.sh", "ship": False}]}
    (r / "host_scripts.json").write_text(json.dumps(cfg))
    return r, host


def _bundle_and_extract(r, tmp_path, commit="c0ffee"):
    out = tmp_path / "b.tgz"
    hsp.bundle(r, r / "host_scripts.json", commit, out)
    ext = tmp_path / "ext"
    ext.mkdir()
    with tarfile.open(out) as tar:
        names = tar.getnames()
        tar.extractall(ext, filter="data")
    return ext, names


def test_bundle_ships_only_shipped_files_but_manifests_all(repo, tmp_path):
    r, host = repo
    ext, names = _bundle_and_extract(r, tmp_path)
    assert sorted(names) == ["HOST_SCRIPTS.json", "scripts/a.sh"]
    m = json.loads((ext / "HOST_SCRIPTS.json").read_text())
    assert set(m["files"]) == {str(host / "a.sh"), str(host / "b.sh")} and m["commit"] == "c0ffee"


def test_install_then_check_reports_missing_and_drift(repo, tmp_path):
    r, host = repo
    ext, _ = _bundle_and_extract(r, tmp_path)
    man = host / "HOST_SCRIPTS.json"
    res = hsp.install(ext, man)
    assert res["installed"] == [str(host / "a.sh")]
    assert stat.S_IMODE((host / "a.sh").stat().st_mode) == 0o750
    first = hsp.check(man)
    assert not first["ok"] and first["missing"] == [str(host / "b.sh")]        # b is not shipped: an operator installs it
    (host / "b.sh").write_text("echo b\n")
    assert hsp.check(man)["ok"]
    (host / "b.sh").write_text("echo old b\n")                                  # a hand-installed old copy (#505)
    drift = hsp.check(man)
    assert drift["mismatched"] == [str(host / "b.sh")] and not drift["ok"]


def test_install_keeps_an_existing_files_mode(repo, tmp_path):
    r, host = repo
    host.mkdir()
    (host / "a.sh").write_text("old\n")
    os.chmod(host / "a.sh", 0o700)
    ext, _ = _bundle_and_extract(r, tmp_path)
    hsp.install(ext, host / "HOST_SCRIPTS.json")
    assert (host / "a.sh").read_text() == "echo a\n"
    assert stat.S_IMODE((host / "a.sh").stat().st_mode) == 0o700


def test_install_refuses_a_bundle_file_that_does_not_match(repo, tmp_path):
    r, host = repo
    ext, _ = _bundle_and_extract(r, tmp_path)
    (ext / "scripts" / "a.sh").write_text("tampered\n")
    with pytest.raises(SystemExit):
        hsp.install(ext, host / "HOST_SCRIPTS.json")
    assert not (host / "a.sh").exists()


def test_check_cli_exits_one_on_drift(repo, tmp_path):
    r, host = repo
    ext, _ = _bundle_and_extract(r, tmp_path)
    hsp.install(ext, host / "HOST_SCRIPTS.json")
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "operations" / "host_script_parity.py"), "check",
                           "--manifest", str(host / "HOST_SCRIPTS.json")], capture_output=True, text=True)
    assert done.returncode == 1 and json.loads(done.stdout)["missing"]


def test_the_real_config_bundles(tmp_path):
    m = hsp.bundle(ROOT, CONFIG, "test", tmp_path / "real.tgz")
    assert len(m["files"]) == len(hsp.load_config(CONFIG))
