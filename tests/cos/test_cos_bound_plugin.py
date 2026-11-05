"""F2 step 2: the cos-bound plugin. Runs its node tests, and fails if the security code copied from MC's minimoi-tools
plugin drifts from the original."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MC = ROOT / "services/mc_operations/plugins/minimoi-tools/index.mjs"
COS = ROOT / "docker/cos-agent-a/plugins/cos-bound/shared.mjs"


def _block(source: str, start: str) -> str:
    i = source.index(start)
    return source[i:source.index("\n}\n", i) + 3]


@pytest.mark.parametrize("start", ["export function runSealer", "export function tokenFromFile",
                                   "async function boundedJson(response)"])
def test_shared_security_code_matches_mc(start):
    assert _block(COS.read_text(), start) == _block(MC.read_text(), start)


def test_seal_constants_match_mc():
    grab = lambda s: re.findall(r"export const (?:SEAL|RUN_ID|CALL_ID) = .*", s)
    assert grab(COS.read_text()) == grab(MC.read_text()) and len(grab(MC.read_text())) == 3


def test_node_suite():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    result = subprocess.run([node, "--test", str(Path(__file__).with_name("cos_bound_plugin.test.mjs"))],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
