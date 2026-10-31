"""Pinned measured CoS production baseline plus the reviewed server-sideband dependency."""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PATH = REPO / "docker" / "requirements.cos-agent.txt"


def _pins():
    pins = {}
    for line in PATH.read_text().splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        match = re.fullmatch(r"([A-Za-z0-9._-]+)==([A-Za-z0-9._+!-]+)", line)
        assert match, f"not pinned exactly: {line!r}"
        pins[match.group(1).lower().replace("_", "-")] = match.group(2)
    return pins


def test_every_cos_dependency_is_pinned_exactly_to_the_measured_production_set():
    pins = _pins()
    # The measured 51-package baseline plus the reviewed server-sideband and register YAML extras, plus the
    # Confer attachment reader's three (pypdf, beautifulsoup4, soupsieve; same pins as the portal), plus the
    # store writer's contract validation and explicit RFC3339 date-time validator (CoS contracts r1).
    assert len(pins) == 62
    assert pins["rfc3339-validator"] == "0.1.4"
    assert {k: pins[k] for k in ("jsonschema", "referencing", "rpds-py", "attrs", "jsonschema-specifications")} == {
        "jsonschema": "4.26.0", "referencing": "0.37.0", "rpds-py": "2026.9.1", "attrs": "26.1.0",
        "jsonschema-specifications": "2025.9.1"}
    assert {k: pins[k] for k in ("pypdf", "beautifulsoup4", "soupsieve")} == {
        "pypdf": "6.8.0", "beautifulsoup4": "4.15.0", "soupsieve": "2.10"}
    assert pins["pyyaml"] == "6.0.3"
    assert pins["websockets"] == "15.0.1"
    # the ten packages a fresh build used to move, held at what production runs
    assert {k: pins[k] for k in ("openai", "pydantic", "pydantic-core", "boto3", "botocore", "cryptography",
                                 "markupsafe", "charset-normalizer", "pycparser", "python-dotenv")} == {
        "openai": "3.22.0", "pydantic": "2.13.5", "pydantic-core": "2.46.5", "boto3": "1.43.104", "botocore": "1.43.104",
        "cryptography": "50.0.1", "markupsafe": "3.0.3", "charset-normalizer": "3.5.1", "pycparser": "3.0",
        "python-dotenv": "1.2.3"}
