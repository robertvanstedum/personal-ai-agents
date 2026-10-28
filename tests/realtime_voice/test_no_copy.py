"""Moved, not copied (Spec 165 §4.1, §12.1): the direct-voice core lives in core/realtime_voice/ only. This fails if any host
file outside it defines its own session ledger (a ConsultLedger or DirectSessions subclass), transcript validator (the
page's event fields checked as a set), summary flow (the summary prompt or its heads and caps) or browser function bridge
(wiring the model's function calls, or posting an owner transcript item). Host files import and adapt; they never copy."""
import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOST_ROOTS = ("minimoi_portal", "domains", "services", "core", "utils", "scripts", "docker")
CORE = ROOT / "core" / "realtime_voice"
SKIP = {"node_modules", "__pycache__", ".git", "venv", ".venv", "dist", "build"}
LEDGERS = {"ConsultLedger", "DirectSessions"}
EVENT_FIELDS = {"item_id", "interrupted", "started_at", "ended_at"}
SUMMARY_WORDS = ("BEGIN_VOICE_RECORDS", "THAT SESSION ONLY")
CORE_CONSTANTS = {"SESSION_MAX_EVENTS", "SESSION_MAX_BYTES", "POST_MAX_EVENTS", "SUMMARY_MAX_TURNS", "SUMMARY_MAX_CHARS",
                  "PROPOSAL_HEAD", "DECISION_HEAD"}
# The browser bridge: the model's function calls wired by a page, a consult body, or the session module's own functions.
BRIDGE = re.compile(r"\bonFunctionCall\b|\bsendFunctionResult\b|\bowner_transcript_item\b"
                    r"|\bfunction\s+(?:directVoice|createCapture|jsonPost)\b"
                    r"|\b(?:const|let|var)\s+(?:directVoice|createCapture|jsonPost)\s*=")


def host_files(suffixes):
    for top in HOST_ROOTS:
        for path in sorted((ROOT / top).rglob("*")):
            if (path.suffix in suffixes and path.is_file() and not SKIP & set(path.parts)
                    and CORE not in path.parents):
                yield path


def python_copies(source, name="<source>"):
    """What a Python host file defines that is the core's: [(line, what)]."""
    found = []
    for node in ast.walk(ast.parse(source, name)):
        if isinstance(node, ast.ClassDef):
            bases = {getattr(b, "id", None) or getattr(b, "attr", None) for b in node.bases}
            if bases & LEDGERS:
                found.append((node.lineno, f"session ledger {node.name}"))
        elif isinstance(node, (ast.Set, ast.List, ast.Tuple, ast.Dict)):
            items = node.keys if isinstance(node, ast.Dict) else node.elts
            words = {i.value for i in items if isinstance(i, ast.Constant) and isinstance(i.value, str)}
            if EVENT_FIELDS <= words:
                found.append((node.lineno, "transcript validator (the page's event fields)"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and any(w in node.value for w in SUMMARY_WORDS):
            found.append((node.lineno, "summary prompt"))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id in CORE_CONSTANTS:
                    found.append((node.lineno, f"summary or capture constant {target.id}"))
    return found


def script_copies(text):
    return [(text.count("\n", 0, m.start()) + 1, f"browser bridge ({m.group(0)})") for m in BRIDGE.finditer(text)]


def test_no_host_python_file_defines_the_cores_ledger_validator_or_summary_flow():
    copies = {}
    for path in host_files({".py"}):
        try:
            found = python_copies(path.read_text(encoding="utf-8"), str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue                                    # not Python this tree runs
        if found:
            copies[str(path.relative_to(ROOT))] = found
    assert copies == {}


def test_no_host_page_or_script_defines_a_browser_bridge():
    copies = {}
    for path in host_files({".js", ".mjs", ".html"}):
        found = script_copies(path.read_text(encoding="utf-8", errors="replace"))
        if found:
            copies[str(path.relative_to(ROOT))] = found
    assert copies == {}


def test_the_core_holds_each_of_them_once():
    """The checks look for the right things: the core itself trips every one, in the files that own them."""
    host = python_copies((CORE / "direct_host.py").read_text())
    kinds = {what.split(" ")[0] for _line, what in host}
    assert {"session", "transcript", "summary"} <= kinds
    assert script_copies((CORE / "static" / "direct-voice-session.js").read_text())


@pytest.mark.parametrize("source", [
    "from core.realtime_voice.consult import ConsultLedger\nclass _Sessions(ConsultLedger):\n    pass\n",
    "import core.realtime_voice.direct_host as h\nclass Mine(h.DirectSessions):\n    pass\n",
    "ALLOWED = {'role', 'item_id', 'index', 'text', 'partial', 'interrupted', 'started_at', 'ended_at'}\n",
    "def check(e):\n    return set(e) - {'item_id', 'interrupted', 'started_at', 'ended_at', 'role'}\n",
    "PROMPT = 'Summarize. BEGIN_VOICE_RECORDS\\n' \n",
    "SUMMARY_MAX_TURNS = 60\n",
])
def test_a_copied_python_piece_is_caught(source):
    assert python_copies(source)


@pytest.mark.parametrize("source", [
    "const c = new RealtimeVoiceController({ onFunctionCall: (call) => consult(call) });",
    "adapter.sendFunctionResult(id, out);",
    "fetch(url, { body: JSON.stringify({ owner_transcript_item: last }) });",
    "export function createCapture({ send }) {}",
    "const jsonPost = (f) => async () => {};",
])
def test_a_copied_bridge_is_caught(source):
    assert script_copies(source)


def test_importing_and_adapting_is_not_a_copy():
    assert not python_copies("from core.realtime_voice.direct_host import DirectVoiceAdapter, install_direct_voice\n"
                             "class _Host(DirectVoiceAdapter):\n    pass\n")
    assert not script_copies("const voice = await loadSession(); voice.directVoice({ page }); voice.jsonPost(fetch, h);")
