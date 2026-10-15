"""Production backups cover Curator's host state and never delete history (#305)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL = (ROOT / "scripts" / "backup_local.sh").read_text()
S3 = (ROOT / "scripts" / "backup_s3.sh").read_text()
MAP = ROOT / "scripts" / "operations" / "curator_state.map"


def _backed_up_data_paths():
    return {
        m.group(1).rstrip("/")
        for m in re.finditer(r'^rsync_path "\$\{DATA_DIR\}/([^"]+)"', LOCAL, re.M)
    }


def test_every_curator_host_path_is_in_the_daily_backup():
    covered = _backed_up_data_paths()
    for line in MAP.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        kind, _old, host, _target = line.split()
        rel = host.removeprefix("data/")
        assert any(rel == c or rel.startswith(c + "/") for c in covered), host


def test_local_prune_only_touches_dated_folders_never_preservation_snapshots():
    prunes = [l for l in LOCAL.splitlines() if "rm -rf" in l]
    assert len(prunes) == 1
    assert "-maxdepth 1" in prunes[0] and "-name '20*-*-*'" in prunes[0]
    assert "curator-preserve" not in prunes[0]


def test_s3_copies_preservation_snapshots_and_never_deletes():
    commands = [l for l in S3.splitlines() if not l.lstrip().startswith("#")]
    assert not any("--delete" in l or "aws s3 rm" in l for l in commands)
    sync = [l for l in S3.splitlines() if "curator-preserve/" in l and "aws s3 sync" in l]
    assert len(sync) == 1
    assert '--exclude "*.partial/*"' in sync[0]
