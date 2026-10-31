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
            if isinstance(volume, dict):
                if volume["type"] != "bind":
                    continue
                source = volume["source"]
            else:
                # The ${...} default holds a ':', so split after its closing brace.
                source = volume[: volume.index(":", volume.index("}"))] if volume.startswith(HOST_DATA) else volume
            if not source.startswith(HOST_DATA):
                continue
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


def test_data_coverage_reads_long_bind_syntax_too(tmp_path, monkeypatch):
    fixture = tmp_path / "compose.yml"
    fixture.write_text(yaml.safe_dump({"services": {"example": {"volumes": [
        {"type": "bind", "source": HOST_DATA + "annotations", "target": "/app/annotations"},
        {"type": "volume", "source": "named-state", "target": "/state"},
        HOST_DATA + "guild:/app/guild:ro",
    ]}}}))
    monkeypatch.setattr(__import__(__name__, fromlist=["COMPOSE"]), "COMPOSE", fixture)
    assert _compose_data_bind_sources() == {("example", "annotations"), ("example", "guild")}


def test_cos_platform_notes_and_reminders_outside_data_are_backed_up():
    """Notes (cos_memory.md) and reminders live beside data/, not in it, so the
    agent that mounts data/ read-write cannot edit their receipts; the daily
    backup must therefore name them explicitly."""
    for src in ('"/opt/minimoi/cos_memory.md"', '"/opt/minimoi/cos_reminders/"'):
        assert re.search(r"^rsync_path " + re.escape(src), LOCAL, re.M), src
    services = yaml.safe_load(COMPOSE.read_text())["services"]
    scheduler = " ".join(str(v) for v in services["cos-scheduler"].get("volumes") or [])
    agent = " ".join(str(v) for v in services["cos-agent-a"].get("volumes") or [])
    assert "/cos_reminders:/app/data/cos_reminders" in scheduler and "/cos_memory.md:" in scheduler
    assert "cos_reminders" not in agent and "cos_memory.md" not in agent
    bot = " ".join(str(v) for v in services["cos-bot"].get("volumes") or [])
    assert "/cos_reminders:/app/data/cos_reminders" in bot          # Telegram /remind, same store


def test_the_cos_store_and_conversation_history_are_in_the_daily_backup():
    # CoS 1.2 spec v0.3 section 4.21: authoritative, outside data/, written only by cos-scheduler.
    assert 'rsync_path "/opt/minimoi/cos/store/"' in LOCAL
    assert 'rsync_path "/opt/minimoi/cos/conversations/"' in LOCAL
