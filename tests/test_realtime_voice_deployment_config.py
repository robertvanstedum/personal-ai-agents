"""Deployment contract for the released realtime voice experience."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SETTING = "VOICE_REALTIME_UI_ENABLED=${VOICE_REALTIME_UI_ENABLED:-1}"


def _compose(filename: str) -> dict:
    return yaml.safe_load((ROOT / filename).read_text(encoding="utf-8"))


def _environment(service: dict) -> list[str]:
    environment = service.get("environment", [])
    assert isinstance(environment, list)
    return environment


def test_production_enables_realtime_voice_for_both_language_domains():
    services = _compose("docker-compose.prod.yml")["services"]

    assert SETTING in _environment(services["german"])
    assert SETTING in _environment(services["portuguese"])


def test_standard_compose_enables_realtime_voice_for_german():
    # Portuguese dev currently runs as a native launchd service; German is the
    # language service defined in the standard Docker Compose file.
    services = _compose("docker-compose.yml")["services"]

    assert SETTING in _environment(services["german"])
