"""One subprocess boundary for Telegram feedback; no credentials at import."""
from pathlib import Path
import subprocess
import sys
from core.telegram.feedback_contract import ACTIONS


def result(outcome, message):
    return {"success": outcome == "confirmed", "outcome": outcome, "message": message}


def write_feedback(action, rank, expected_url, *, root=None, run=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    script = root / "domains/curator/curator_feedback.py"
    if action not in ACTIONS or type(rank) is not int or rank <= 0 or not expected_url:
        return result("refused", "Invalid feedback request; nothing was written")
    if not script.is_file():
        return result("refused", "Feedback writer is unavailable; nothing was written")
    interpreter = root / "venv/bin/python"
    args = [str(interpreter) if interpreter.is_file() else sys.executable, str(script), action, str(rank),
            "--channel", "telegram", "--text", f"{action}d from Telegram", "--expected-url", expected_url]
    try:
        completed = (run or subprocess.run)(args, capture_output=True, cwd=str(root), timeout=30)
    except Exception:
        return result("uncertain", "Feedback outcome is unknown; check the briefing before trying again")
    if completed.returncode == 0:
        return result("confirmed", f"Article #{rank} {action}d")
    # Nonzero may follow a durable write. Never infer retry safety from it.
    return result("uncertain", "Feedback was refused or could not be confirmed; request a fresh briefing")
