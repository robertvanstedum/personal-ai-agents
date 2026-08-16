"""Deployment contract for the released realtime voice experience."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SETTING = "VOICE_REALTIME_UI_ENABLED=${VOICE_REALTIME_UI_ENABLED:-1}"


def _service_block(filename: str, service_name: str) -> str:
    text = (ROOT / filename).read_text(encoding="utf-8")
    marker = f"  {service_name}:\n"
    start = text.index(marker) + len(marker)
    remainder = text[start:]
    next_service = re.search(r"(?m)^  [^\s][^:]*:\s*$", remainder)
    return remainder if next_service is None else remainder[: next_service.start()]


def test_production_enables_realtime_voice_for_both_language_domains():
    assert SETTING in _service_block("docker-compose.prod.yml", "german")
    assert SETTING in _service_block("docker-compose.prod.yml", "portuguese")


def test_standard_compose_enables_realtime_voice_for_german():
    # Portuguese dev currently runs as a native launchd service; German is the
    # language service defined in the standard Docker Compose file.
    assert SETTING in _service_block("docker-compose.yml", "german")


def test_cos_receives_voice_provider_credential_at_platform_boundary():
    service = _service_block("docker-compose.yml", "cos")

    assert "OPENAI_API_KEY=${OPENAI_API_KEY:-}" in service
    assert "COS_AGENT_RUNTIME_URL=" in service
