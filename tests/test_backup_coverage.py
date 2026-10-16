"""Production backups cover Curator's host state and never delete history (#305)."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
LOCAL = (ROOT / "scripts" / "backup_local.sh").read_text()
S3 = (ROOT / "scripts" / "backup_s3.sh").read_text()
MAP = ROOT / "scripts" / "operations" / "curator_state.map"
COMPOSE = ROOT / "docker-compose.prod.yml"
HOST_DATA = "${MINIMOI_ROOT:-/opt/minimoi}/data/"


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


def _compose_data_bind_sources():
    """Host paths under data/ that a production container mounts.

    The bind of all of data/ (cos-agent-a) is left out; it is covered by the
    paths beneath it.
    """
    services = yaml.safe_load(COMPOSE.read_text())["services"]
    sources = set()
    for name, service in services.items():
        for volume in service.get("volumes") or []:
            if not volume.startswith(HOST_DATA):
                continue
            # The ${...} default holds a ':', so the source ends at the first
            # ':' after the closing brace.
            source = volume[: volume.index(":", volume.index("}"))]
            sources.add((name, source.removeprefix(HOST_DATA)))
    return sources


def test_every_production_data_bind_source_is_in_the_daily_backup():
    covered = _backed_up_data_paths()
    sources = _compose_data_bind_sources()
    rels = {rel for _name, rel in sources}
    # The guild binds (portal, cos-bot, cos-scheduler, cos-agent-a evidence)
    # and the gateway receipts must be among what this test checks.
    assert {"guild", "guild/cos_context.json", "guild/limits",
            "model_gateway_receipts.jsonl"} <= rels
    for name, rel in sorted(sources):
        assert any(rel == c or rel.startswith(c + "/") for c in covered), (name, rel)


def test_guild_receipts_and_research_dives_are_in_the_daily_backup():
    assert {"guild", "model_gateway_receipts.jsonl", "research_dives"} <= _backed_up_data_paths()


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
