"""The domain images run anthropic 2.3.0, whose Messages.create takes no temperature: a Claude call passing one raises TypeError."""
import ast
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _sources():
    names = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    return [n for n in names if not n.startswith(("tests/", "_NewDomains/", "prototype-lab/"))]


def test_no_claude_call_passes_temperature():
    found = []
    for name in _sources():
        try:
            tree = ast.parse((ROOT / name).read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("create", "stream") \
                    and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "messages" \
                    and any(k.arg == "temperature" for k in node.keywords):
                found.append(f"{name}:{node.lineno}")
    assert not found, found


@pytest.mark.parametrize("name", ["curator", "german", "portuguese", "telegram"])
def test_domain_images_pin_the_anthropic_sdk(name):
    text = (ROOT / f"docker/requirements.{name}.txt").read_text()
    assert re.search(r"^anthropic==\d+\.\d+\.\d+$", text, re.M), name
