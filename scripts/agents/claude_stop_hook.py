#!/usr/bin/env python3
"""Claude Code Stop hook (.claude/settings.json): every turn here ends with a MINIMOI-STOP line (AGENTS.md, #365).

Reads the hook's JSON on stdin and the session transcript's last assistant message.
  * a stop line is there: filed for MC's check-ins (same key as stop.py's, so a line stop.py filed is not doubled);
  * none, first time: asks Claude once to end with one (exit 2, reason on stderr), so it is never silently missing;
  * none again (stop_hook_active): filed as 'unreported', and the turn ends.
It never fails a turn: any error of its own exits 0.
"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stop  # noqa: E402

ASK = ("End this turn with your stop line (AGENTS.md, Stop line): one last line `MINIMOI-STOP {\"state\": ..., "
       "\"needs\": ..., \"next\": ...}`, state one of done | blocked | awaiting_owner | awaiting_review | waiting_ci | "
       "error. Waiting on Robert? `python3 scripts/agents/stop.py ask \"<exact decision>\"`, then run "
       "`python3 scripts/agents/stop.py wait --key <key>` in the background. Waiting on CI or a review? Run "
       "`stop.py wait` in the background too, so a nudge wakes you.")


def last_assistant_text(transcript, max_lines=400):
    lines = Path(transcript).read_text(errors="replace").splitlines()[-max_lines:]
    for raw in reversed(lines):
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if entry.get("type") != "assistant":
            continue
        content = (entry.get("message") or {}).get("content")
        if isinstance(content, str):
            return content
        texts = [c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text"]
        if texts:
            return "\n".join(texts)
    return ""


def main():
    try:
        event = json.loads(sys.stdin.read() or "{}")
        session = event.get("session_id") or ""
        text = last_assistant_text(event["transcript_path"]) if event.get("transcript_path") else ""
        fields = stop.parse_line(text)
        if fields is None and not event.get("stop_hook_active"):
            sys.stderr.write(ASK)
            return 2
        branch = stop.branch(event.get("cwd"))
        if fields is None:
            fields = {"state": "unreported", "needs": "", "next": ""}
            line = "unreported:" + (event.get("transcript_path") or "") + ":" + str(len(text))
        else:
            line = stop.PREFIX + json.dumps({k: v for k, v in fields.items()}, sort_keys=True, ensure_ascii=False)
        fields["branch"] = branch
        stop.file_stop("claude-code", session, fields, line)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
