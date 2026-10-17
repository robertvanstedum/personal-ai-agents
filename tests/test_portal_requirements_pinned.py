"""The portal image is reproducible: every dependency pinned exactly, the base image pinned by digest, and the
production baseline (measured in the running portal at 60c553f7) kept unchanged."""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASELINE = {
    "beautifulsoup4": "4.15.0", "blinker": "1.9.0", "boto3": "1.43.111", "botocore": "1.43.111", "certifi": "2026.7.22",
    "charset-normalizer": "3.5.2", "click": "8.5.0", "flask": "3.1.3", "flask-cors": "6.0.2", "idna": "3.20",
    "itsdangerous": "2.2.0", "jinja2": "3.1.6", "jmespath": "1.1.0", "markdown": "3.11", "markupsafe": "3.0.4",
    "psycopg2-binary": "2.9.13", "python-dateutil": "2.9.0.post0", "python-dotenv": "1.2.4", "requests": "2.32.5",
    "s3transfer": "0.19.2", "sentry-sdk": "2.71.0", "six": "1.17.0", "soupsieve": "2.10", "typing_extensions": "4.16.0",
    "urllib3": "2.8.0", "werkzeug": "3.1.9",
}
# Reviewed file-first work-register YAML parser; all older pins stay exact.
REGISTER_ADDED = {"pyyaml": "6.0.3"}
UI_ADDED = {"nh3", "markdown-it-py", "mdurl", "pillow", "pypdf"}
# Reviewed server-owned MC realtime SDK closure; every existing baseline pin stays fixed.
MANAGED_REALTIME_ADDED = {
    "openai": "3.22.0", "websockets": "15.0.1", "sniffio": "1.3.1",
    "truststore": "0.10.5", "typing-inspection": "0.4.4", "annotated-types": "0.8.0",
    "anyio": "4.15.1", "pydantic": "2.14.1", "httpx2": "2.13.1", "jiter": "0.17.0",
    "h11": "0.16.0", "pydantic_core": "2.50.1", "httpcore2": "2.13.1",
}


def _pins():
    pins = {}
    for line in (REPO / "docker" / "requirements.portal.txt").read_text().splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        match = re.fullmatch(r"([A-Za-z0-9._-]+)(?:\[[^\]]+\])?==([A-Za-z0-9._+!-]+)", line)
        assert match, f"not pinned exactly: {line!r}"
        pins[match.group(1).lower()] = match.group(2)
    return pins


def test_every_portal_dependency_is_pinned_exactly():
    assert _pins()


def test_production_baseline_unchanged_and_only_reviewed_ui_and_realtime_packages_added():
    pins = _pins()
    assert {name: pins.get(name) for name in BASELINE} == BASELINE
    assert set(pins) - set(BASELINE) == UI_ADDED | set(MANAGED_REALTIME_ADDED) | set(REGISTER_ADDED)
    assert {name: pins.get(name) for name in MANAGED_REALTIME_ADDED} == MANAGED_REALTIME_ADDED
    assert {name: pins.get(name) for name in REGISTER_ADDED} == REGISTER_ADDED


def test_the_base_image_is_pinned_by_digest():
    first = next(line for line in (REPO / "docker" / "Dockerfile.portal").read_text().splitlines()
                 if line.startswith("FROM "))
    assert re.fullmatch(r"FROM python:3\.12-slim@sha256:[0-9a-f]{64}", first), first
