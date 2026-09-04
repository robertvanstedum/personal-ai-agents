"""requirements.lock must pin exactly what requirements.txt asks for.

The Dockerfile installs the lock; requirements.txt keeps the human-edited
ranges. This test proves every top-level requirement (and its extras) appears
in the lock as an exact ``==`` pin that satisfies the declared range, that the
lock contains only exact pins, and that nothing is pinned twice.
"""
import re

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

from conftest import BASE_DIR

LOCK_LINE = re.compile(r"^([A-Za-z0-9_.\-]+)==([A-Za-z0-9_.+!]+)$")
# Extras map to the distribution that provides them.
EXTRA_DISTRIBUTIONS = {("psycopg", "binary"): "psycopg-binary"}


def read_lines(name):
    return [
        line.strip()
        for line in (BASE_DIR / name).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def lock_pins():
    pins = {}
    for line in read_lines("requirements.lock"):
        match = LOCK_LINE.match(line)
        assert match, f"lock line is not an exact pin: {line!r}"
        name = canonicalize_name(match.group(1))
        assert name not in pins, f"{name} pinned twice"
        pins[name] = Version(match.group(2))
    return pins


def test_lock_contains_only_exact_unique_pins():
    pins = lock_pins()
    assert len(pins) >= 20
    assert "pip" not in pins  # provided by the pinned base image


def test_every_top_level_requirement_is_pinned_within_its_range():
    pins = lock_pins()
    for line in read_lines("requirements.txt"):
        requirement = Requirement(line)
        name = canonicalize_name(requirement.name)
        assert name in pins, f"{requirement.name} missing from requirements.lock"
        assert requirement.specifier.contains(pins[name], prereleases=True), (
            f"{name}=={pins[name]} violates {line}"
        )
        for extra in requirement.extras:
            provider = EXTRA_DISTRIBUTIONS.get((name, extra))
            if provider:
                assert canonicalize_name(provider) in pins, f"{provider} (extra {extra}) missing"
                assert pins[canonicalize_name(provider)] == pins[name]


def test_dockerfile_installs_the_lock_from_a_digest_pinned_base():
    dockerfile = (BASE_DIR / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^FROM python:3\.12-slim@sha256:[0-9a-f]{64}$", dockerfile, re.M)
    assert "pip install -r requirements.lock" in dockerfile
