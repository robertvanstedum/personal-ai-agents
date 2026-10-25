import json
import shutil
import subprocess
from pathlib import Path
import pytest
from minimoi_portal.guild_ui.board_text import normalized, WHITESPACE, TextRefused

ROOT = Path(__file__).resolve().parents[3]
VALUES = ["hello", "ä", "a\u0308", "👨‍👩‍👧‍👦", "😀" * 280, "😀" * 281,
          "x\r\ny\rz", "\x1c hello \u0085", "", " ", "\ud800", "x" * 279 + "\r\n"]

@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_browser_and_server_count_same_scalars():
    node = shutil.which("node")
    module = (ROOT / "minimoi_portal/guild_ui/static/js/board_text.js").as_uri()
    code = "import {inspectText} from " + json.dumps(module) + "; console.log(JSON.stringify(" + json.dumps(VALUES) + ".map(inspectText)))"
    result = subprocess.run([node, "--input-type=module", "-e", code], text=True, capture_output=True, check=True)
    answers = json.loads(result.stdout)
    for raw, answer in zip(VALUES, answers):
        try:
            text = normalized(raw)
        except TextRefused:
            assert answer["code"] == "invalid_text"
            continue
        assert answer["count"] == len(text)
        assert answer["text"] == text.strip(WHITESPACE)
        assert answer["valid"] == (len(text) <= 280 and bool(text.strip(WHITESPACE)))
