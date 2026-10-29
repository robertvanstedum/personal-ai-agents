"""Test-only controls for the fake Claude Code and Codex CLIs.

A connection gives its child an environment built from the launch policy (core/mc_delegation_runner/launch_env.py),
never the test's own environment, so the fakes cannot be steered through environment variables. Instead each fake
reads ``fake_controls.json`` beside its script, and records the environment NAMES the connection gave it (never the
values) when ``FAKE_ENV_OUT`` is set.
"""
from __future__ import annotations

import json
from pathlib import Path

CONTROLS_FILE = "fake_controls.json"

# Prepended to every fake CLI script. It runs before the fake's own code.
CONTROLS_PRELUDE = r'''
import json as _json, os as _os
_GIVEN = sorted(_os.environ)                          # the names the connection gave this process, before the test's controls
_GIVEN_LEDGER = _os.environ.get("MINIMOI_AGENT_REQUESTS_DIR")
try:
    with open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "fake_controls.json")) as _fh:
        _os.environ.update(_json.load(_fh))
except FileNotFoundError:
    pass
if _os.environ.get("FAKE_ENV_OUT"):
    with open(_os.environ["FAKE_ENV_OUT"], "a") as _fh:
        _fh.write(_json.dumps({"names": _GIVEN, "ledger": _GIVEN_LEDGER}) + "\n")
'''


def fake_controls(folder, **values: str) -> None:
    """Set controls (FAKE_MODE, FAKE_SID, ...) for the fakes whose scripts live in ``folder``; later calls add to them."""
    path = Path(folder) / CONTROLS_FILE
    current = json.loads(path.read_text()) if path.exists() else {}
    current.update({k: str(v) for k, v in values.items()})
    path.write_text(json.dumps(current))


def given_environments(path) -> list[dict]:
    """What each started fake recorded: {"names": [...], "ledger": <MINIMOI_AGENT_REQUESTS_DIR or None>}."""
    try:
        return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    except FileNotFoundError:
        return []
