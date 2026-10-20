"""Exercise the provider wrapper without making requests or starting an agent."""
from pathlib import Path
import shutil
import subprocess
import pytest

def test_cutoff_stream_contract():
    node=shutil.which("node")
    if node is None:pytest.skip("Node required for OpenClaw plugin tests")
    result=subprocess.run([node,"--test",str(Path(__file__).with_name("mc_cutoff_stream.test.mjs"))],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
