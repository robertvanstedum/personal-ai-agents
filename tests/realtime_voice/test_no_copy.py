"""Moved, not copied (Spec 165 §4.1, §12.1), checked by architecture, not by a ban on names (review R1 P3-9, R2 S5).

1. The import graph and definitions. Every Python file outside core/realtime_voice is read for what it imports, with each
   alias resolved (``from core.realtime_voice.consult import ConsultLedger as L`` makes ``L`` that class). A host class may
   not derive from the core's session ledger (ConsultLedger, DirectSessions); a host's adapter or store (a subclass of
   DirectVoiceAdapter or DirectVoiceStore, however imported) may define only §4.4's hooks, which are read from the core
   itself, plus its own private helpers; and a module that imports the direct-voice core may not define a function or
   class under one of the core's flow names (the validator, the dispatcher, the summary flow, the tool texts). The
   summary prompt's delimiters stay the core's alone.
2. The field-literal rule is gone: a store that writes records with the page's event fields (A2's CoS JSONL store) is a
   store, not a copy.
3. tests/realtime_voice/test_direct_contract.py runs the shared contract over every adapter (the test adapter, MC's and,
   since A2, Chief of Staff's).
4. The scan covers every directory a host could live in, prototype-lab, _NewDomains and static included.

The browser side: a host page or script may not wire the model's function calls or post an owner transcript item itself
(the session module does), comments aside; and the session module reads the controller only through its public
methods (item 11)."""
import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOST_ROOTS = ("minimoi_portal", "domains", "services", "core", "utils", "scripts", "docker", "prototype-lab",
              "_NewDomains", "static", "tools")
CORE = ROOT / "core" / "realtime_voice"
SKIP = {"node_modules", "__pycache__", ".git", "venv", ".venv", "dist", "build"}
DIRECT = "core.realtime_voice.direct_host"
LEDGERS = {"core.realtime_voice.consult.ConsultLedger", f"{DIRECT}.DirectSessions"}
INTERFACES = (f"{DIRECT}.DirectVoiceAdapter", f"{DIRECT}.DirectVoiceStore")
# The core's flow: a host module that imports the core never defines these itself.
FLOW = {"events", "closing", "summary_prompt", "summary_texts", "summarize", "summary_system", "consult", "dispatch",
        "same_key", "settled_result", "keep_receipt", "install_direct_voice", "tools", "direct_rules", "done_intent",
        "affirmative", "compose_instructions", "same_record", "operation_id", "decision_head", "Receipts",
        "DirectSessions", "ConsultLedger", "GatewaySummary"}
SUMMARY_WORDS = ("BEGIN_VOICE_RECORDS", "THAT SESSION ONLY")
# The browser bridge: the model's function calls wired by a page, a consult body, or the session module's own functions.
BRIDGE = re.compile(r"\bonFunctionCall\b|\bsendFunctionResult\b|\bowner_transcript_item\b"
                    r"|\bfunction\s+(?:directVoice|createCapture|jsonPost)\b"
                    r"|\b(?:const|let|var)\s+(?:directVoice|createCapture|jsonPost)\s*=")
# The controller's private fields: the session module reads the controller only through its public methods (Spec 165 §4.3).
PRIVATE_READ = re.compile(r"\._(?:state|sessionId|adapter)\b")


def hooks():
    """§4.4's hooks, read from the core: what an adapter or a store may define."""
    from core.realtime_voice import direct_host
    return {iface: {n for n in dir(getattr(direct_host, iface.rsplit(".", 1)[1])) if not n.startswith("__")}
            for iface in INTERFACES}


def host_files(suffixes):
    for top in HOST_ROOTS:
        for path in sorted((ROOT / top).rglob("*")):
            if (path.suffix in suffixes and path.is_file() and not SKIP & set(path.parts)
                    and CORE not in path.parents):
                yield path


def aliases(tree):
    """{local name: the dotted path it stands for}, from the module's imports."""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for a in node.names:
                out[a.asname or a.name] = f"{node.module}.{a.name}"
    return out


def resolve(expr, names):
    """The dotted path a base-class expression stands for (Name or Attribute chain), through the module's aliases."""
    parts = []
    while isinstance(expr, ast.Attribute):
        parts.append(expr.attr)
        expr = expr.value
    if not isinstance(expr, ast.Name):
        return None
    head = names.get(expr.id, expr.id)
    return ".".join([head, *reversed(parts)])


def python_copies(source, name="<source>", allowed=None):
    """What a Python host file defines that is the core's: [(line, what)]."""
    allowed = allowed if allowed is not None else hooks()
    tree = ast.parse(source, name)
    names = aliases(tree)
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | set(names.values())
    imports_core = any(path == DIRECT or path.startswith(DIRECT + ".") for path in imported)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            bases = {resolve(b, names) for b in node.bases}
            if bases & LEDGERS:
                found.append((node.lineno, f"session ledger {node.name}"))
            for iface in INTERFACES:
                if iface in bases:
                    for item in node.body:
                        defined = ([item.name] if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) else
                                   [t.id for t in getattr(item, "targets", [getattr(item, "target", None)])
                                    if isinstance(t, ast.Name)] if isinstance(item, (ast.Assign, ast.AnnAssign)) else [])
                        for what in defined:
                            if what not in allowed[iface] and not what.startswith("_"):
                                found.append((item.lineno, f"{node.name}.{what} beyond §4.4's hooks"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and any(w in node.value for w in SUMMARY_WORDS):
            found.append((node.lineno, "summary prompt"))
    if imports_core:
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in FLOW:
                found.append((node.lineno, f"the core's {node.name}, defined again"))
    return found


def script_copies(text):
    code = re.sub(r"(?<![:\\])//[^\n]*", "", text)                    # comments aside (a URL's // is kept)
    return [(code.count("\n", 0, m.start()) + 1, f"browser bridge ({m.group(0)})") for m in BRIDGE.finditer(code)]


def test_no_host_python_file_copies_the_cores_ledger_flow_or_widens_its_hooks():
    copies, allowed = {}, hooks()
    for path in host_files({".py"}):
        try:
            found = python_copies(path.read_text(encoding="utf-8"), str(path), allowed)
        except (SyntaxError, UnicodeDecodeError, ValueError):
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


def test_mcs_adapter_and_store_are_hosts_that_import_the_core_and_define_only_its_hooks():
    """The real host is read like any other: it imports the core, and its adapter and store stay inside §4.4."""
    source = (ROOT / "minimoi_portal/guild_ui/mc_voice_direct.py").read_text()
    names = aliases(ast.parse(source))
    assert names["DirectVoiceAdapter"] == f"{DIRECT}.DirectVoiceAdapter" and names["install_direct_voice"].startswith(DIRECT)
    assert python_copies(source) == []


def test_cos_adapter_and_store_are_hosts_that_import_the_core_and_define_only_its_hooks():
    """A2: Chief of Staff's adapter and file store are read the same way as MC's."""
    for module in ("domains/cos/direct_voice.py", "domains/cos/voice_store.py"):
        source = (ROOT / module).read_text()
        names = aliases(ast.parse(source))
        assert any(path.startswith(DIRECT) for path in names.values()), module
        assert python_copies(source) == [], module
    names = aliases(ast.parse((ROOT / "domains/cos/direct_voice.py").read_text()))
    assert names["DirectVoiceAdapter"] == f"{DIRECT}.DirectVoiceAdapter" and names["install_direct_voice"].startswith(DIRECT)


def private_reads(text):
    """[(line, field)] where a script reads one of the controller's private fields (comments aside)."""
    code = re.sub(r"//[^\n]*", "", text)
    return [(code.count("\n", 0, m.start()) + 1, m.group(0)) for m in PRIVATE_READ.finditer(code)]


def test_the_session_module_reads_no_private_controller_field():
    """The shared session module uses only the controller's public methods (sessionId(), state(), speak(),
    speakTracked()), so a controller change cannot silently break it (A1 fix round, item 11)."""
    assert private_reads((CORE / "static" / "direct-voice-session.js").read_text()) == []


@pytest.mark.parametrize("source", ["if (c._state === 'connecting') {}", "const id = controller._sessionId;",
                                    "c._adapter.sendContinuationInstruction(x);"])
def test_a_private_read_is_caught(source):
    assert private_reads(source)


def test_the_core_holds_each_of_them_once():
    """The checks look for the right things: the core itself trips them, in the files that own them."""
    assert python_copies((CORE / "direct_host.py").read_text())
    assert script_copies((CORE / "static" / "direct-voice-session.js").read_text())


@pytest.mark.parametrize("source", [
    "from core.realtime_voice.consult import ConsultLedger\nclass _Sessions(ConsultLedger):\n    pass\n",
    "import core.realtime_voice.direct_host as h\nclass Mine(h.DirectSessions):\n    pass\n",
    # R2 S5: an alias does not hide the base class
    "from core.realtime_voice.consult import ConsultLedger as L\nclass X(L):\n    pass\n",
    "from core.realtime_voice import direct_host as dh\nclass Y(dh.DirectSessions):\n    pass\n",
    # an adapter or store that grows past §4.4
    "from core.realtime_voice.direct_host import DirectVoiceAdapter as A\nclass H(A):\n    def admit(self, sid):\n        pass\n",
    "from core.realtime_voice.direct_host import DirectVoiceStore\nclass S(DirectVoiceStore):\n    def summarize(self):\n        pass\n",
    # the core's flow, defined again in a host that imports the core
    "from core.realtime_voice.direct_host import DirectVoiceAdapter\ndef events(body, started, clock):\n    return []\n",
    "import core.realtime_voice.direct_host\ndef summary_prompt(turns):\n    return ''\n",
    "PROMPT = 'Summarize. BEGIN_VOICE_RECORDS\\n' \n",
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


def test_importing_adapting_and_storing_records_is_not_a_copy():
    allowed = "\n".join([
        "from core.realtime_voice.direct_host import DirectVoiceAdapter, DirectVoiceStore, install_direct_voice",
        "class _Host(DirectVoiceAdapter):",
        "    person = {}", "    summary_key = 'voice-summary-cos'", "    def owner(self):", "        return None",
        "    def typed_turn(self, *a, **kw):", "        return {}", "    def _helper(self):", "        return 1",
        "class _Store(DirectVoiceStore):",
        "    def put(self, handle, sid, record):", "        return None",
        "    def keep(self, *a, **kw):",
        # R2 S5: a store writing the page's event fields is a store, not a validator (A2's CoS JSONL store)
        "        row = {'item_id': 1, 'started_at': 2, 'ended_at': 3, 'interrupted': False, 'role': 'user'}",
        "        return row",
        # R2 S5: a host constant that happens to share a core constant's name is not a copy
        "PROPOSAL_HEAD = 'a host label'", "DECISION_HEAD = {}",
    ])
    assert python_copies(allowed) == []
    assert not script_copies("const voice = await loadSession(); voice.directVoice({ page }); voice.jsonPost(fetch, h);\n"
                             "// the session module posts owner_transcript_item and wires onFunctionCall, not this page")


def test_the_scan_reaches_every_directory_a_host_could_live_in():
    for top in ("prototype-lab", "_NewDomains", "static"):
        assert top in HOST_ROOTS and (ROOT / top).is_dir(), top
