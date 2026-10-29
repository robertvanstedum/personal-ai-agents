"""Claude Code hook -> the agent census (host runtime spec v0.5 R6.6-c, §6.6). BUILT, NEVER INSTALLED.

Installing it in any Claude Code settings file is a configuration change Robert approves (R6.6-c); nothing in S1
installs it. It lives in the runtime's bundle (``core/mc_delegation_runner``), not beside the Stop hook in
``scripts/agents/``: the installed copy then sits outside any checkout (a branch switch never changes it, as for the
runner itself), it writes the census format of the runtime that sends it (the bundle digest covers both), and it is
not part of any service image.

It reads one hook payload on stdin and appends the census events it means to the host runtime's census folder:

    SessionStart   the session's root row: vendor anthropic, role session, the transcript path the payload names,
                   start_path and route from the command line (the door that installed it says how it was started)
    PreToolUse     activity: running
    Stop           idle (the turn ended; the session waits)
    SubagentStart  a subagent of the session (its agent_id and agent_type)    } OFF until confirmed: these names and
    SubagentStop   its end, observed                                         } payloads are unverified (N4)

These three session events are consumed in the repo already (``Stop``: scripts/agents/claude-stop-hook.settings.json;
``SessionStart`` with ``session_id``, ``cwd``, ``transcript_path``: scripts/operations/claude_probe_profile.py;
``PreToolUse``: core/agent_requests/claude_send_guard.py), so their rows say ``verified: true``. ``SubagentStart`` and
``SubagentStop`` are used nowhere in the repo: ``SUBAGENT_HOOKS_CONFIRMED`` stays False until their names and payloads
are confirmed against the installed Claude Code version, and until then the session row says ``children:
not_exposed`` (never "no children") and a subagent payload writes nothing. Turning it on is a reviewed code change,
never a flag.

Only ids, the event's name and the transcript path are read. The payload's prompt, tool input, messages and every
other field are never looked at, so no content, environment value or secret reaches the census. It never fails or
holds up a turn: it prints nothing, and any error of its own exits 0.

The settings it would be installed with (for review; the door that starts the session passes them, e.g. ``claude
--settings``, never the user's own settings file):

    {"hooks": {
      "SessionStart": [{"hooks": [{"type": "command", "timeout": 5, "command":
        "/usr/bin/python3 -I <bundle>/core/mc_delegation_runner/claude_hook.py --census <artifact_root>/census --start-path fallback --route subscription"}]}],
      "PreToolUse":   [{"hooks": [{"type": "command", "timeout": 5, "command": "<the same>"}]}],
      "Stop":         [{"hooks": [{"type": "command", "timeout": 5, "command": "<the same>"}]}]}}

Stdlib only; Python 3.9-compatible; run as a file (``python -I``), so the census module is loaded by its path.
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

SUBAGENT_HOOKS_CONFIRMED = False          # N4: unverified; a reviewed change turns it on, after confirmation
SOURCE = "claude.hook"
PAYLOAD_MAX = 64 * 1024


def _census():
    here = Path(__file__).resolve().with_name("census.py")
    spec = importlib.util.spec_from_file_location("minimoi_census", str(here))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def events(payload, *, start_path, route, census=None) -> list:
    """The (kind, body) pairs one payload means, under this hook's switches."""
    census = census or _census()
    return census.hook_events(payload, source=SOURCE, verified=True, subagents=SUBAGENT_HOOKS_CONFIRMED,
                              vendor="anthropic", start_path=start_path, route=route, subagent_verified=False)


def main(argv=None, stdin=None) -> int:
    try:
        p = argparse.ArgumentParser(prog="claude_hook", add_help=False)
        p.add_argument("--census", required=True)
        p.add_argument("--start-path", required=True, choices=("connection", "fallback"))
        p.add_argument("--route", required=True)
        a = p.parse_args(argv)
        raw = (stdin or sys.stdin).read(PAYLOAD_MAX + 1)
        if len(raw) > PAYLOAD_MAX or not Path(a.census).is_absolute():
            return 0
        census = _census()
        out = census.CensusFile(a.census)
        for kind, body in events(json.loads(raw or "{}"), start_path=a.start_path, route=a.route, census=census):
            out.append(kind, body)
    except BaseException:
        return 0                                  # a hook of the census never fails a turn
    return 0


if __name__ == "__main__":
    sys.exit(main())
