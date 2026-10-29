"""Both supported import paths preserve the shared payment-text contract.

Synthetic cases with literal expected output, independent of implementation.
"""
import subprocess
import sys
from pathlib import Path

import pytest

from minimoi_portal.guild_ui import payment_scrub as portal
from utils import payment_scrub as shared


@pytest.mark.parametrize("module", [shared, portal], ids=["shared", "portal"])
@pytest.mark.parametrize("text, expected", [
    ("", ""),
    ("My visa 2027 renewal", "My visa 2027 renewal"),
    ("ordinary\u00a0text", "ordinary\u00a0text"),
    ("note: billing@example.com ok", "note: [payment detail removed] ok"),
    ("note: 4242 4242 4242 4242 ok", "note: [payment detail removed] ok"),
    ("note: ４２４２ ４２４２ ４２４２ ４２４２ ok", "note: [payment detail removed] ok"),
    ("note: 4242\u200b4242\u200b4242\u200b4242 ok", "note: [payment detail removed] ok"),
    ("routing 021000021", "[payment detail removed]"),
    ("card 4242/4242/4242/4242", "card [payment detail removed]"),
    ("[payment detail removed]", "[payment detail removed]"),
    (None, "None"),
    (42, "42"),
])
def test_existing_results_and_change_flag(module, text, expected):
    assert module.REMOVED == "[payment detail removed]"
    assert module.scrub(text) == expected
    assert module.scrubbed(text) is (expected != text)
    assert module.scrub(expected) == expected


@pytest.mark.parametrize("module", [shared, portal], ids=["shared", "portal"])
def test_existing_luhn_test_import(module):
    assert module._luhn_ok("4242424242424242")
    assert not module._luhn_ok("1727450000123")


def test_portal_uses_the_shared_implementation():
    assert portal.scrub is shared.scrub
    assert portal.scrubbed is shared.scrubbed
    assert portal._luhn_ok is shared._luhn_ok


def test_workshop_cli_runs_without_site_packages_or_repository_cwd(tmp_path):
    script = Path(__file__).resolve().parents[3] / "tools/workshop/topic_inbox.py"
    # -I ignores PYTHONPATH; -S excludes site packages, including Flask.
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(script), "--data", str(tmp_path / "guild"), "verify"],
        cwd=tmp_path, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
