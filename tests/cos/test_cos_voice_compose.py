"""Her voice folder in production compose (Spec 165 v0.3 §6.5, §12.2; A2 item 5): cos-scheduler alone mounts
${MINIMOI_ROOT}/cos_voice at COS_VOICE_DIR, and no mount of cos-agent-a reaches it, in the production file or any staging
overlay (the release check's automated part: her runtime cannot read live voice records). Her summary key defaults empty,
so a deploy without it still starts. No Docker is run."""
from __future__ import annotations

import posixpath
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
PROD = REPO / "docker-compose.prod.yml"
ROOT = "/opt/minimoi"
VOICE = ROOT + "/cos_voice"


def services(path):
    return yaml.safe_load(path.read_text()).get("services") or {}


def sources(service):
    """Each bind mount's host path, with ${MINIMOI_ROOT…} as production sets it; named volumes have none."""
    out = []
    for volume in service.get("volumes") or []:
        text = (volume.get("source") or "") if isinstance(volume, dict) else str(volume)
        for form in ("${MINIMOI_ROOT:-/opt/minimoi}", "${MINIMOI_ROOT}"):
            text = text.replace(form, ROOT)
        if text.startswith("${MINIMOI_ROOT:?"):
            text = ROOT + text[text.index("}") + 1:]
        source = text.split(":", 1)[0]
        if source.startswith("/"):
            out.append(posixpath.normpath(source))
    return out


def reaches(source, target=VOICE):
    return target == source or target.startswith(source.rstrip("/") + "/")


def test_cos_scheduler_alone_mounts_her_voice_folder_at_cos_voice_dir():
    prod = services(PROD)
    scheduler = prod["cos-scheduler"]
    assert "${MINIMOI_ROOT:-/opt/minimoi}/cos_voice:/app/data/cos-voice" in scheduler["volumes"]
    assert "COS_VOICE_DIR=/app/data/cos-voice" in scheduler["environment"]
    others = {name for name, svc in prod.items() if name != "cos-scheduler" and any(reaches(s) for s in sources(svc))}
    assert others == set()


def test_no_mount_of_her_runtime_reaches_the_voice_folder_in_production_or_staging():
    files = [PROD, *sorted(REPO.glob("docker-compose.staging*.yml"))]
    for path in files:
        agent = services(path).get("cos-agent-a") or {}
        assert not [s for s in sources(agent) if reaches(s)], path.name
    agent = services(PROD)["cos-agent-a"]
    assert sources(agent) and ROOT + "/data" in sources(agent)            # the check reads real mounts: data/ is hers
    assert not reaches(ROOT + "/data") and not any(s == ROOT for s in sources(agent))


def test_her_summary_key_defaults_empty_so_a_deploy_without_it_still_starts():
    environment = services(PROD)["cos-scheduler"]["environment"]
    (line,) = [e for e in environment if e.startswith("MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_COS=")]
    assert line == "MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_COS=${MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_COS:-}" and ":?" not in line


def test_her_voice_switches_are_not_turned_on_by_compose():
    for name, service in services(PROD).items():
        environment = " ".join(service.get("environment") or [])
        assert "MINIMOI_COS_VOICE_DIRECT=1" not in environment and "MINIMOI_COS_DIRECT_VOICE_PRODUCTION=1" not in environment, name
